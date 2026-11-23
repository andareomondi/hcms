"""
Visit intake, split by responsibility.

open_visit()     Reception opens the visit and charges the fees. No clinical data here.
triage_encounter() A triage nurse records vitals and complaint, sets priority, assigns the doctor,
                 and queues the patient. Triage is not held for payment; the queue entry waits
                 at AWAITING_PAYMENT if fees are unpaid, as before.

Lives in its own module so the services module does not grow, and so there is no import cycle.
"""
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core_patients.models import Patient
from hospital_system.validation import ValidationError as _VE
from notifications.services import notify
from wards.models import Admission

from .models import Encounter, QueueEntry, TriageVitals
from .services import (
    FlowError,
    activate_visit,
    charge_visit_fees,
    notify_billing_ready_for_bypass,
    patient_label,
)

PRIORITIES = (1, 2, 3)
PAY_WALL_EXEMPT_TYPES = ("DELIVERY", "CHILD_HEALTH", "INPATIENT")

TRIAGE_VITAL_RULES = {
    "temperature_c": ("Temperature (°C)", dict(low=30, high=44, dec=True)),
    "pulse_bpm": ("Pulse (beats per minute)", dict(low=20, high=260)),
    "systolic_mmhg": ("Systolic blood pressure", dict(low=40, high=260)),
    "diastolic_mmhg": ("Diastolic blood pressure", dict(low=20, high=200)),
    "respiratory_rate": ("Respiratory rate", dict(low=4, high=80)),
    "spo2_percent": ("Oxygen saturation (%)", dict(low=40, high=100)),
    "weight_kg": ("Weight (kg)", dict(low=0.5, high=400, dec=True)),
}


def validate_triage_vitals(vitals):
    """Range-check every vital that was given. Blank means not measured, which is allowed."""
    from hospital_system import validation as v
    clean = {}
    for field, value in vitals.items():
        if field == "notes":
            clean[field] = (value or "").strip()[:2000]
            continue
        if field not in TRIAGE_VITAL_RULES or value is None or value == "":
            clean[field] = None
            continue
        label, rule = TRIAGE_VITAL_RULES[field]
        if rule.get("dec"):
            number = v.parse_decimal(value, label, places=1 if field == "temperature_c" else 2)
        else:
            number = v.parse_int(value, label)
        clean[field] = v.in_range(number, label, rule["low"], rule["high"])
    sys_, dia = clean.get("systolic_mmhg"), clean.get("diastolic_mmhg")
    if sys_ is not None and dia is not None and dia >= sys_:
        raise _VE("Diastolic blood pressure must be lower than systolic. Check the entry.")
    return clean

PREGNANCY_TYPES = ("ANC", "DELIVERY", "MATERNITY")
STAY_TYPE = "WARD_STAY"
REVIEW_TYPE = "INPATIENT"

def active_admission_for(patient):
    return Admission.objects.filter(patient=patient, discharged_at__isnull=True).first()


def _refuse_pregnancy_visit_for_male(patient, encounter_type):
    """
    A pregnancy or birth visit can never be opened for a patient whose sex is male.
    Child health visits are not restricted: boys attend them too.
    """
    if encounter_type in PREGNANCY_TYPES and patient.sex == "M":
        raise FlowError("Antenatal and delivery visits cannot be opened for a patient whose sex is male. "
                        "Check the patient's record.")


def awaiting_triage():
    """Open visits with no queue entry, oldest first. Ward stays are not triage visits."""
    return (Encounter.objects.filter(status="OPEN", queue_entries__isnull=True)
            .exclude(encounter_type__in=[STAY_TYPE, "SURGERY"])
            .select_related("patient")
            .order_by("opened_at"))


@transaction.atomic
def open_visit(*, patient, encounter_type, payer_type, staff):
    valid_types = dict(Encounter.TYPE_CHOICES)
    if encounter_type not in valid_types or encounter_type == STAY_TYPE:
        raise FlowError("Choose a valid visit type.")
    if encounter_type == "SURGERY":
        raise FlowError("Surgery is booked through the theatre, not opened as a visit.")

    Patient.objects.select_for_update().get(pk=patient.pk)

    admission = active_admission_for(patient)
    if admission is not None and encounter_type != REVIEW_TYPE:
        raise FlowError("This patient is in a ward bed. Open an inpatient review instead.")
    if admission is None and encounter_type == REVIEW_TYPE:
        raise FlowError("An inpatient review is only for a patient admitted to a ward by a doctor.")

    open_review = (Encounter.objects.filter(patient=patient, status="OPEN")
                   .exclude(encounter_type=STAY_TYPE)
                   .order_by("opened_at").first())
    if open_review is not None:
        if open_review.queue_entries.exists():
            raise FlowError("This patient already has an open visit that has not been closed yet. "
                            "Finish or close that visit before opening a new one.")
        raise FlowError("This patient already has a visit waiting for triage. "
                        "Triage that visit instead of opening a new one.")

    _refuse_pregnancy_visit_for_male(patient, encounter_type)
    encounter = Encounter.objects.create(
        patient=patient, encounter_type=encounter_type,
        payer_type=payer_type, opened_by=staff,
    )
    if encounter_type not in PAY_WALL_EXEMPT_TYPES:
        charge_visit_fees(encounter, staff)
    return encounter


@transaction.atomic
def triage_encounter(*, encounter, staff, doctor, priority, vitals, chief_complaint,
                     bypass_reason=""):
    """
    Triage nurse: records vitals and complaint, sets priority, assigns the doctor.
    `vitals` is a dict of TriageVitals fields. Priority 3 or an EMERGENCY visit is treated
    before payment and must carry a reason.
    """
    enc = Encounter.objects.select_for_update().get(pk=encounter.pk)
    _refuse_pregnancy_visit_for_male(enc.patient, enc.encounter_type)
    if enc.status != "OPEN":
        raise FlowError("This visit is closed.")
    if enc.queue_entries.exists():
        raise FlowError("This patient has already been triaged.")
    if priority not in PRIORITIES:
        raise FlowError("Choose a valid priority.")
    from duty.commitments import require_on_duty_doctor
    from duty.services import DutyError
    try:
        require_on_duty_doctor(doctor)
    except DutyError as exc:
        raise FlowError(str(exc))

    emergency = priority == 3 or enc.encounter_type == "EMERGENCY"
    bypass_reason = (bypass_reason or "").strip()
    if emergency and not bypass_reason:
        raise FlowError("An emergency is treated before payment. Give the reason.")

    enc.chief_complaint = (chief_complaint or "").strip()
    if emergency:
        enc.prepay_bypassed = True
        enc.prepay_bypass_reason = bypass_reason
        enc.prepay_bypass_by = staff
        enc.prepay_bypass_at = timezone.now()
    enc.save(update_fields=["chief_complaint", "prepay_bypassed", "prepay_bypass_reason",
                            "prepay_bypass_by", "prepay_bypass_at"])

    try:
        clean = validate_triage_vitals(vitals)
    except _VE as exc:
        raise FlowError(str(exc))
    TriageVitals.objects.create(encounter=enc, recorded_by=staff, **clean)

    entry = QueueEntry.objects.create(
        encounter=enc,
        assigned_doctor=doctor,
        priority=priority,
        status="AWAITING_PAYMENT",
    )

    if emergency:
        entry.status = "WAITING"
        entry.save(update_fields=["status", "updated_at"])
        notify_billing_ready_for_bypass(enc)
        notify([doctor], kind="PATIENT_ASSIGNED",
               message=f"Emergency: {patient_label(enc)} (priority {priority}).",
               link=reverse("doctors_portal:dashboard"))
    else:
        # Fees may already be paid at reception; if so, the patient is queued at once.
        activate_visit(enc)

    return enc, entry, emergency
