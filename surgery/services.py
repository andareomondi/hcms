"""Surgery rules. Views call these; nothing else decides whether a case is booked or cancelled."""
from django.db import transaction
from django.utils import timezone

from billing import services as billing
from core_patients.models import Patient, PatientAllergy
from dashboard.audit_actions import AuditAction
from dashboard.utils import create_audit_entry
from triage_queue.models import Encounter

from .models import SurgicalCase

MIN_CLEARANCE_NOTE = 20
MIN_CANCEL_REASON = 20


class SurgeryError(ValueError):
    """A refusal with a message the user can act on."""


@transaction.atomic
def book_case(*, patient, procedure, category, booked_by, indication_encounter=None,
              scheduled_for=None, allergy_clearance_note="", ip=None):
    """
    Books a standalone surgical case. Creates the billing container (a closed SURGERY encounter) and
    charges the procedure fee to it.
    """
    if category not in dict(SurgicalCase.CATEGORY_CHOICES):
        raise SurgeryError("Choose elective or emergency.")
    if not procedure.is_active:
        raise SurgeryError("This procedure is not in use.")

    Patient.objects.select_for_update().get(pk=patient.pk)

    if category == "ELECTIVE":
        if indication_encounter is None:
            raise SurgeryError("An elective operation needs the consultation or admission that decided on it.")
        if indication_encounter.patient_id != patient.pk:
            raise SurgeryError("The indication must belong to the same patient.")
        if indication_encounter.encounter_type == "SURGERY":
            raise SurgeryError("A surgical case cannot be its own indication.")

    if PatientAllergy.objects.filter(patient=patient, status="ACTIVE").exists():
        note = (allergy_clearance_note or "").strip()
        if booked_by.role != "DOCTOR":
            raise SurgeryError("This patient has a recorded allergy. A doctor must book the operation.")
        if len(note) < MIN_CLEARANCE_NOTE:
            raise SurgeryError(f"This patient has a recorded allergy. Give a note of at least "
                               f"{MIN_CLEARANCE_NOTE} characters explaining why surgery is safe.")
        allergy_note = note
        allergy_by = booked_by
    else:
        allergy_note, allergy_by = "", None

    container = Encounter.objects.create(
        patient=patient, encounter_type="SURGERY", payer_type="SELF_PAY",
        opened_by=booked_by, status="CLOSED", closed_at=timezone.now(),
    )
    case = SurgicalCase.objects.create(
        patient=patient, procedure=procedure, category=category,
        indication_encounter=indication_encounter,
        billing_encounter=container, scheduled_for=scheduled_for, booked_by=booked_by,
        allergy_clearance_by=allergy_by, allergy_clearance_note=allergy_note,
    )
    billing.add_charge(
        encounter=container, source_type="SURGERY",
        description=f"Surgery: {procedure.name}", unit_price=procedure.price,
        staff=booked_by, key=f"surgery-{case.pk}",
    )
    create_audit_entry(actor=booked_by.user, action_type=AuditAction.SURGERY_BOOKED,
                       description=f"Booked {category.lower()} {procedure.name} for {patient}.",
                       ip_address=ip)
    return case


@transaction.atomic
def cancel_case(*, case, cancelled_by, reason, ip=None):
    """The record is kept with the reason. Nothing is deleted."""
    case = SurgicalCase.objects.select_for_update().get(pk=case.pk)
    reason = (reason or "").strip()
    if len(reason) < MIN_CANCEL_REASON:
        raise SurgeryError(f"Give a reason of at least {MIN_CANCEL_REASON} characters.")
    if case.status != "BOOKED":
        raise SurgeryError("Only a booked case can be cancelled.")
    case.status = "CANCELLED"
    case.cancelled_by = cancelled_by
    case.cancelled_at = timezone.now()
    case.cancel_reason = reason
    case.save(update_fields=["status", "cancelled_by", "cancelled_at", "cancel_reason"])
    create_audit_entry(actor=cancelled_by.user, action_type=AuditAction.SURGERY_CANCELLED,
                       description=f"Cancelled surgery {case.procedure.name} for {case.patient}: {reason}",
                       ip_address=ip)
    return case
