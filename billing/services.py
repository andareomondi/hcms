from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from billing.models import Refund
from core_patients.models import PatientRelationship

from .models import BillingAccount, Charge, Payment, PaymentAllocation

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

BLOCKING_SOURCES = ("MEDICATION_ORDER", "SCAN_ORDER", "LAB_TEST")
FEE_SOURCES = ("REGISTRATION", "CONSULTATION")


# ---------- accounts and holds ----------

def billing_owner_for(patient):
    """
    The person who pays. Minors and patients with an unknown date of birth pay through
    their active guarantor when one exists (spec 10.2). Adults pay for themselves.
    """
    if patient.is_minor is False:
        return patient
    guarantor = (PatientRelationship.objects
                 .filter(patient=patient, is_billing_guarantor=True, is_active=True)
                 .select_related("related_patient").first())
    return guarantor.related_patient if guarantor else patient


def billing_hold_reason(patient):
    if patient.is_minor is True and not PatientRelationship.objects.filter(
            patient=patient, is_billing_guarantor=True, is_active=True).exists():
        return ("This child has no paying guardian linked. Open the child's Family page, "
                "choose 'They are the parent or guardian', search for them, and tick "
                "'This person pays the bills'.")
    return None


def billing_account_for(patient):
    account, _ = BillingAccount.objects.get_or_create(owner=billing_owner_for(patient))
    return account


# ---------- charges ----------

def add_charge(*, encounter, source_type, description, unit_price, staff,
               quantity=Decimal(1), source_id=None, key=None):
    """
    Create a PROVISIONAL charge. If `key` already exists, return the existing
    charge instead of creating a duplicate (idempotent).
    """
    if key:
        existing = Charge.objects.filter(idempotency_key=key).first()
        if existing:
            return existing

    unit_price = Decimal(unit_price).quantize(CENT)
    quantity = Decimal(quantity)
    amount = (unit_price * quantity).quantize(CENT)
    patient = encounter.patient
    return Charge.objects.create(
        encounter=encounter,
        treated_patient=patient,
        billing_account=billing_account_for(patient),
        source_type=source_type,
        source_id=source_id,
        idempotency_key=key,
        description=description[:255],
        quantity=quantity,
        unit_price=unit_price,
        amount=amount,
        status="PROVISIONAL",
        created_by=staff,
    )


def void_or_reverse(charge, staff):
    """
    PROVISIONAL charges are voided. POSTED charges are never edited or deleted:
    a reversal row is added instead (spec 10.1).
    """
    if charge.status == "PROVISIONAL":
        charge.status = "VOIDED"
        charge.save(update_fields=["status"])
        return None
    if charge.status == "POSTED":
        return Charge.objects.create(
            encounter=charge.encounter,
            treated_patient=charge.treated_patient,
            billing_account=charge.billing_account,
            source_type=charge.source_type,
            source_id=charge.source_id,
            description=f"Reversal: {charge.description}"[:255],
            quantity=charge.quantity,
            unit_price=-charge.unit_price,
            amount=-charge.amount,
            reversal_of=charge,
            status="POSTED",
            posted_at=timezone.now(),
            created_by=staff,
        )
    return None


def medication_charge_or_none(order):
    return (Charge.objects.filter(idempotency_key=f"med:{order.id}")
            .exclude(status="VOIDED").first())


def lab_charge_or_none(line):
    return (Charge.objects.filter(idempotency_key=f"lab:{line.id}")
            .exclude(status="VOIDED").first())

# ---------- payment state per line ----------

def paid_amount(charge):
    return (PaymentAllocation.objects.filter(charge=charge)
            .aggregate(t=Sum("amount"))["t"] or ZERO)


def outstanding_amount(charge):
    return charge.amount - paid_amount(charge)


def charge_fully_paid(charge):
    return charge.amount > 0 and paid_amount(charge) >= charge.amount


@transaction.atomic
def post_charge_for_release(charge, staff):
    """Billing posts one charge. Refused for the person who created it (spec 10.5)."""
    charge = Charge.objects.select_for_update().get(pk=charge.pk)
    if charge.status != "PROVISIONAL":
        return charge
    if charge.created_by_id == staff.id:
        raise ValueError("A charge cannot be posted by the person who created it.")
    charge.status = "POSTED"
    charge.posted_at = timezone.now()
    charge.save(update_fields=["status", "posted_at"])
    return charge


# ---------- allocation ----------

def allocation_order(encounter, lines):
    """
    Emergency-bypassed encounters: oldest first.
    Otherwise: fee lines first, then blocking lines (medicines and scans), then the rest oldest-first.
    """
    if encounter.prepay_bypassed:
        return sorted(lines, key=lambda c: c.created_at)

    def rank(c):
        if c.source_type in FEE_SOURCES:
            return (0, c.created_at)
        if c.source_type in BLOCKING_SOURCES:
            return (1, c.created_at)
        return (2, c.created_at)

    return sorted(lines, key=rank)


def open_lines(encounter):
    """Lines with money still owed, in allocation order. Voided lines are excluded."""
    lines = list(Charge.objects.filter(encounter=encounter).exclude(status="VOIDED"))
    owed = [c for c in lines if outstanding_amount(c) > 0]
    return allocation_order(encounter, owed)


def preview_allocation(encounter, amount):
    """Read-only. What a payment of `amount` would settle, line by line."""
    remaining = Decimal(amount)
    rows = []
    for c in open_lines(encounter):
        if remaining <= 0:
            break
        take = min(remaining, outstanding_amount(c))
        rows.append({"charge": c, "take": take})
        remaining -= take
    return rows, remaining  # remaining > 0 means the amount exceeds the outstanding balance


@transaction.atomic
def allocate_payment(*, payment, encounter, amount, staff):
    """
    Settles lines in allocation order. A provisional line is posted by billing first.
    A line the caller created is skipped and returned, so the caller can refuse the payment.
    Raises ValueError if the amount exceeds what is owed.
    """
    remaining = Decimal(amount)
    skipped = []
    for c in open_lines(encounter):
        if remaining <= 0:
            break
        if c.status == "PROVISIONAL":
            if c.created_by_id == staff.id:
                skipped.append(c)
                continue
            c = post_charge_for_release(c, staff)
        take = min(remaining, outstanding_amount(c))
        PaymentAllocation.objects.create(payment=payment, charge=c, amount=take)
        remaining -= take
        if charge_fully_paid(c):
            c.status = "PAID"
            c.save(update_fields=["status"])
    if remaining > 0:
        raise ValueError("Payment exceeds the outstanding balance.")
    return skipped


# ---------- totals ----------

def patient_totals(patient):
    charges = Charge.objects.filter(treated_patient=patient).exclude(status="VOIDED")
    paid_in = Payment.objects.filter(encounter__patient=patient).aggregate(t=Sum("amount"))["t"] or ZERO
    refunded = Refund.objects.filter(encounter__patient=patient).aggregate(t=Sum("amount"))["t"] or ZERO
    total = charges.aggregate(t=Sum("amount"))["t"] or ZERO
    paid = paid_in - refunded
    return {"total": total, "paid": paid, "outstanding": total - paid}


def encounter_tally(encounter):
    charges = Charge.objects.filter(encounter=encounter).exclude(status="VOIDED")
    provisional = charges.filter(status="PROVISIONAL").aggregate(t=Sum("amount"))["t"] or ZERO
    posted = charges.filter(status__in=["POSTED", "PAID"]).aggregate(t=Sum("amount"))["t"] or ZERO
    paid_in = Payment.objects.filter(encounter=encounter).aggregate(t=Sum("amount"))["t"] or ZERO
    refunded = Refund.objects.filter(encounter=encounter).aggregate(t=Sum("amount"))["t"] or ZERO
    paid = paid_in - refunded
    return {
        "provisional": provisional,
        "posted": posted,
        "paid": paid,
        "refunded": refunded,
        "total": provisional + posted,
        "outstanding": provisional + posted - paid,
    }

def post_encounter_charges(encounter, staff):
    """
    Legacy close-time posting. Not used for release under QOL-2; kept for the data review in QOL-2.
    """
    return (Charge.objects.filter(encounter=encounter, status="PROVISIONAL")
            .exclude(created_by=staff)
            .update(status="POSTED", posted_at=timezone.now()))
