import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from triage_queue.models import Encounter

from . import services as billing
from .models import Charge, Payment

MPESA_CODE = re.compile(r"^[A-Z0-9]{8,12}$")


class PaymentError(Exception):
    """A rule the cashier must see. The message is shown as-is."""


def _receipt_number():
    import uuid
    return f"RCP-{timezone.now():%y%m%d}-{uuid.uuid4().hex[:6].upper()}"


@transaction.atomic
def take_payment(*, encounter, method, amount_raw, reference, payer_phone, staff):
    if method not in ("MANUAL", "MPESA"):
        raise PaymentError("Choose Manual or M-Pesa.")
    try:
        amount = Decimal(str(amount_raw)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        raise PaymentError("Enter a valid amount.")
    if amount <= 0:
        raise PaymentError("The amount must be greater than zero.")

    reference = (reference or "").strip().upper()
    if method == "MPESA":
        if not MPESA_CODE.match(reference):
            raise PaymentError("Enter the M-Pesa transaction code (8 to 12 letters and digits).")
        if Payment.objects.filter(method="MPESA", reference=reference).exists():
            raise PaymentError("This M-Pesa code has already been recorded.")

    reason = billing.billing_hold_reason(encounter.patient)
    if reason:
        raise PaymentError(reason)

    # Lock the visit so two cashiers cannot pay the same balance twice
    encounter = Encounter.objects.select_for_update().get(pk=encounter.pk)

    # Provisional charges become payable once billing reviews them
    billing.post_encounter_charges(encounter, staff)

    outstanding = billing.encounter_tally(encounter)["outstanding"]
    if outstanding <= 0:
        raise PaymentError("Nothing is outstanding on this visit.")
    if amount > outstanding:
        raise PaymentError(f"The amount is more than the outstanding KES {outstanding:,.2f}.")

    payment = Payment.objects.create(
        receipt_number=_receipt_number(),
        billing_account=billing.billing_account_for(encounter.patient),
        encounter=encounter,
        amount=amount,
        method=method,
        reference=reference if method == "MPESA" else reference[:60],
        payer_phone=(payer_phone or "")[:20],
        recorded_by=staff,
    )

    if billing.encounter_tally(encounter)["outstanding"] == 0:
        Charge.objects.filter(encounter=encounter, status="POSTED").update(status="PAID")
    return payment
