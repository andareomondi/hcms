from decimal import Decimal

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from notifications.services import notify_on_duty

from . import services as billing
from .models import Charge, Refund

ZERO = Decimal("0.00")


class RefundError(Exception):
    """A refund rule the billing officer must see. Shown as-is."""


def _request_refund(*, encounter, anchor, amount, reason, staff):
    refund = Refund.objects.create(
        charge=anchor,
        encounter=encounter,
        billing_account=anchor.billing_account,
        amount=amount,
        reason=reason,
        requested_by=staff,
    )
    notify_on_duty(
        role="BILLING", kind="SYSTEM",
        message=f"Refund due: KES {amount:,.2f} to {encounter.patient}. {reason[:80]}",
        link=reverse("billing:refunds"),
    )
    return refund


@transaction.atomic
def close_order_charge(*, charge, staff, reason):
    """
    Closes the charge of a medicine that will not be supplied (cancelled or short).
    - Nothing paid: the charge is voided (or reversed, if already posted).
    - Money paid: the charge stays on the bill and a refund is requested for the amount paid.
      The refund bill is posted when the refund is approved.
    """
    if charge is None:
        return None
    charge = Charge.objects.select_for_update().get(pk=charge.pk)
    if charge.status == "VOIDED":
        return None

    paid = billing.paid_amount(charge)
    if paid == 0:
        billing.void_or_reverse(charge, staff)
        return None
    return _request_refund(encounter=charge.encounter, anchor=charge, amount=paid,
                           reason=reason, staff=staff)


@transaction.atomic
def apply_unused_stay(*, encounter, anchor, unused, staff, reason):
    """
    A bed stay ended early. The unused amount is settled in two parts:
    - The part already paid is refunded (a refund request, posted on approval).
    - The part not yet paid reduces the bill (a provisional credit for billing to post).
    """
    if unused <= 0:
        return None
    if anchor is None:
        raise RefundError("No bed-day charge found for this stay.")

    t = billing.encounter_tally(encounter)
    owed_after = t["total"] - unused
    surplus = t["paid"] - t["refund_pending"] - owed_after
    refund_part = min(unused, max(surplus, ZERO))
    credit_part = unused - refund_part

    if credit_part > 0:
        Charge.objects.create(
            encounter=encounter,
            treated_patient=anchor.treated_patient,
            billing_account=anchor.billing_account,
            source_type="REFUND",
            source_id=anchor.source_id,
            idempotency_key=f"stay-credit:{anchor.id}",
            description=f"Unused stay credit: {reason}"[:255],
            quantity=1,
            unit_price=-credit_part,
            amount=-credit_part,
            status="PROVISIONAL",
            created_by=staff,
        )

    if refund_part > 0:
        return _request_refund(encounter=encounter, anchor=anchor, amount=refund_part,
                               reason=reason, staff=staff)
    return None


@transaction.atomic
def pay_refund(*, refund, staff, method, reference=""):
    """
    Approves and pays out a refund. Posts a negative refund bill, already paid, so the
    visit total drops by the refund. The parent bill is marked paid once it is fully settled.
    The person who requested a refund cannot pay it out.
    """
    refund = (Refund.objects.select_for_update()
              .select_related("charge", "encounter", "billing_account")
              .get(pk=refund.pk))
    if refund.status == "PAID":
        raise RefundError("This refund has already been paid out.")
    if refund.requested_by_id == staff.id:
        raise RefundError("The person who requested a refund cannot pay it out.")
    if method not in ("MANUAL", "MPESA"):
        raise RefundError("Choose Manual or M-Pesa.")
    reference = (reference or "").strip().upper()
    if method == "MPESA" and not reference:
        raise RefundError("Enter the M-Pesa transaction code for this payout.")

    now = timezone.now()
    Charge.objects.create(
        encounter=refund.encounter,
        treated_patient=refund.charge.treated_patient,
        billing_account=refund.billing_account,
        source_type="REFUND",
        source_id=refund.id,
        idempotency_key=f"refund:{refund.id}",
        description=f"Refund paid out: {refund.reason}"[:255],
        quantity=1,
        unit_price=-refund.amount,
        amount=-refund.amount,
        status="PAID",
        posted_at=now,
        created_by=staff,
    )

    parent = Charge.objects.select_for_update().get(pk=refund.charge_id)
    if parent.status != "PAID" and billing.charge_fully_paid(parent):
        parent.status = "PAID"
        parent.save(update_fields=["status"])

    refund.status = "PAID"
    refund.paid_by = staff
    refund.paid_at = now
    refund.method = method
    refund.reference = reference[:60]
    refund.save(update_fields=["status", "paid_by", "paid_at", "method", "reference"])
    return refund


def refunds_paid_for(encounter):
    """Total refunds paid out on a visit."""
    return billing.refunds_paid_out(encounter)
