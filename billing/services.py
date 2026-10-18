from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import BillingAccount, Charge

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def billing_account_for(patient):
    account, _ = BillingAccount.objects.get_or_create(owner=patient)
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


def encounter_tally(encounter):
    qs = Charge.objects.filter(encounter=encounter).exclude(status="VOIDED")
    provisional = qs.filter(status="PROVISIONAL").aggregate(t=Sum("amount"))["t"] or ZERO
    posted = qs.filter(status="POSTED").aggregate(t=Sum("amount"))["t"] or ZERO
    return {"provisional": provisional, "posted": posted, "total": provisional + posted}


def post_encounter_charges(encounter, staff):
    """Billing officers post provisional charges. Nobody posts a charge they created (spec 10.5)."""
    return (Charge.objects.filter(encounter=encounter, status="PROVISIONAL")
            .exclude(created_by=staff)
            .update(status="POSTED", posted_at=timezone.now()))
