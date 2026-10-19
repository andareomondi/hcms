from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from core_patients.models import PatientRelationship

from .models import BillingAccount, Charge, Payment

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


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
    """
    A child with no guarantor cannot be paid for yet. Charges are still recorded.
    Returns the reason text, or None if the account can be billed.
    """
    if patient.is_minor is True and not PatientRelationship.objects.filter(
            patient=patient, is_billing_guarantor=True, is_active=True).exists():
        return ("This child has no billing guarantor. Link a parent or guardian "
                "in the family view before taking payment.")
    return None

def billing_account_for(patient):
    account, _ = BillingAccount.objects.get_or_create(owner=billing_owner_for(patient))
    return account

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

def patient_totals(patient):
    charges = Charge.objects.filter(treated_patient=patient).exclude(status="VOIDED")
    paid = Payment.objects.filter(encounter__patient=patient).aggregate(t=Sum("amount"))["t"] or ZERO
    total = charges.aggregate(t=Sum("amount"))["t"] or ZERO
    return {"total": total, "paid": paid, "outstanding": total - paid}


def encounter_tally(encounter):
    charges = Charge.objects.filter(encounter=encounter).exclude(status="VOIDED")
    provisional = charges.filter(status="PROVISIONAL").aggregate(t=Sum("amount"))["t"] or ZERO
    posted = charges.filter(status__in=["POSTED", "PAID"]).aggregate(t=Sum("amount"))["t"] or ZERO
    paid = Payment.objects.filter(encounter=encounter).aggregate(t=Sum("amount"))["t"] or ZERO
    return {
        "provisional": provisional,
        "posted": posted,
        "paid": paid,
        "total": provisional + posted,
        "outstanding": provisional + posted - paid,
    }
def post_encounter_charges(encounter, staff):
    """Billing officers post provisional charges. Nobody posts a charge they created (spec 10.5)."""
    return (Charge.objects.filter(encounter=encounter, status="PROVISIONAL")
            .exclude(created_by=staff)
            .update(status="POSTED", posted_at=timezone.now()))
