import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from core_patients.models import Patient, PatientRelationship
from triage_queue.models import Encounter
from triage_queue.services import activate_visit

from . import services as billing
from .models import Payment

MPESA_CODE = re.compile(r"^[A-Z0-9]{8,12}$")


class PaymentError(Exception):
    """A rule the cashier must see. The message is shown as-is."""


def _receipt_number():
    import uuid
    return f"RCP-{timezone.now():%y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def resolve_payer(*, encounter, payer_type, payer_patient_id="", payer_name="",
                  payer_relationship="", payer_id_number="", payer_phone=""):
    """
    Works out who is paying. Nothing here restricts who may pay, only what is recorded.
    - SELF: the patient themself, child or adult.
    - GUARDIAN: a patient in the system with an active link to this patient.
    - OTHER: any adult. Name, relationship and ID number are required.
    Returns a dict of Payment fields.
    """
    patient = encounter.patient
    if payer_type == "SELF":
        return {"payer_type": "SELF", "payer_patient": patient,
                "payer_name": f"{patient.first_name} {patient.last_name}".strip(),
                "payer_relationship": "Self", "payer_id_number": "", "payer_phone": payer_phone}

    if payer_type == "GUARDIAN":
        payer = Patient.objects.filter(pk=payer_patient_id).first() if payer_patient_id else None
        if payer is None:
            raise PaymentError("Choose the guardian who is paying.")
        link = (PatientRelationship.objects.filter(is_active=True)
                .filter(patient=patient, related_patient=payer)
                .first()
                or PatientRelationship.objects.filter(is_active=True,
                                                      patient=payer, related_patient=patient).first())
        if link is None:
            raise PaymentError("That person is not linked to this patient. Record them as Other adult, "
                               "or link them on the Family page first.")
        return {"payer_type": "GUARDIAN", "payer_patient": payer,
                "payer_name": f"{payer.first_name} {payer.last_name}".strip(),
                "payer_relationship": link.get_relationship_display(),
                "payer_id_number": payer.id_number or "", "payer_phone": payer_phone}

    if payer_type == "OTHER":
        name = (payer_name or "").strip()
        rel = (payer_relationship or "").strip()
        id_no = (payer_id_number or "").strip()
        if not name or not rel:
            raise PaymentError("Record the name and relationship of the person paying.")
        if not id_no:
            raise PaymentError("Record the ID number of the person paying.")
        return {"payer_type": "OTHER", "payer_patient": None, "payer_name": name[:150],
                "payer_relationship": rel[:60], "payer_id_number": id_no[:60],
                "payer_phone": payer_phone}

    raise PaymentError("Choose who is paying: the patient, a registered guardian, or another adult.")


@transaction.atomic
def take_payment(*, encounter, method, amount_raw, reference, payer_phone, staff,
                 payer_type="SELF", payer_patient_id="", payer_name="",
                 payer_relationship="", payer_id_number=""):
    """
    Records a payment and allocates it line by line (QOL-2). The payer is recorded (QOL-4)
    and is not restricted. The bill stays on the patient's account.
    Returns (payment, cleared_now).
    """
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

    payer = resolve_payer(encounter=encounter, payer_type=payer_type,
                          payer_patient_id=payer_patient_id, payer_name=payer_name,
                          payer_relationship=payer_relationship,
                          payer_id_number=payer_id_number, payer_phone=payer_phone)

    encounter = Encounter.objects.select_for_update().get(pk=encounter.pk)
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
        recorded_by=staff,
        payer_phone=(payer["payer_phone"] or "")[:20],
        payer_type=payer["payer_type"],
        payer_patient=payer["payer_patient"],
        payer_name=payer["payer_name"],
        payer_relationship=payer["payer_relationship"],
        payer_id_number=payer["payer_id_number"],
    )
    try:
        skipped = billing.allocate_payment(payment=payment, encounter=encounter,
                                           amount=amount, staff=staff)
    except ValueError as exc:
        raise PaymentError(str(exc))
    if skipped:
        raise PaymentError("Some lines were created by you and cannot be posted by you: "
                           + ", ".join(c.description for c in skipped)
                           + ". Another billing officer must post them.")

    activate_visit(encounter)

    cleared_now = False
    if (encounter.prepay_bypassed and encounter.prepay_cleared_at is None
            and billing.encounter_tally(encounter)["outstanding"] == 0):
        encounter.prepay_cleared_at = timezone.now()
        encounter.prepay_cleared_by = staff
        encounter.save(update_fields=["prepay_cleared_at", "prepay_cleared_by"])
        cleared_now = True
    return payment, cleared_now
