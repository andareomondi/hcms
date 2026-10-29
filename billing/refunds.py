from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from notifications.services import notify_on_duty

from . import services as billing
from .models import Charge, Refund


class RefundError(Exception):
    """A refund rule the billing officer must see. Shown as-is."""


@transaction.atomic
def close_order_charge(*, charge, staff, reason):
    """
    Closes the charge of a medicine that will not be supplied (cancelled or short).
    - Nothing paid: the charge is voided (or reversed, if already posted).
    - Money paid: the charge is voided and a refund is requested for the amount paid.
    Returns the Refund created, or None.
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

    charge.status = "VOIDED"
    charge.save(update_fields=["status"])
    refund = Refund.objects.create(
        charge=charge,
        encounter=charge.encounter,
        billing_account=charge.billing_account,
        amount=paid,
        reason=reason,
        requested_by=staff,
    )
    notify_on_duty(
        role="BILLING", kind="SYSTEM",
        message=f"Refund due: KES {paid:,.2f} to {charge.treated_patient}. {reason[:80]}",
        link=reverse("billing:refunds"),
    )
    return refund


@transaction.atomic
def pay_refund(*, refund, staff, method, reference=""):
    """Billing pays a pending refund out. The person who requested it cannot pay it out."""
    refund = Refund.objects.select_for_update().get(pk=refund.pk)
    if refund.status == "PAID":
        raise RefundError("This refund has already been paid out.")
    if refund.requested_by_id == staff.id:
        raise RefundError("The person who requested a refund cannot pay it out.")
    if method not in ("MANUAL", "MPESA"):
        raise RefundError("Choose Manual or M-Pesa.")
    reference = (reference or "").strip().upper()
    if method == "MPESA" and not reference:
        raise RefundError("Enter the M-Pesa transaction code for this payout.")

    refund.status = "PAID"
    refund.paid_by = staff
    refund.paid_at = timezone.now()
    refund.method = method
    refund.reference = reference[:60]
    refund.save(update_fields=["status", "paid_by", "paid_at", "method", "reference"])
    return refund


def refunds_paid_for(encounter):
    """Total refunds on a visit, pending or paid. A refund reduces what the patient has paid."""
    from django.db.models import Sum
    return Refund.objects.filter(encounter=encounter).aggregate(t=Sum("amount"))["t"] or 0
