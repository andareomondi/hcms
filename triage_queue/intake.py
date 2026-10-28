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

from notifications.services import notify

from .models import Encounter, QueueEntry, TriageVitals
from .services import (
    FlowError,
    activate_visit,
    charge_visit_fees,
    notify_billing_ready_for_bypass,
    patient_label,
)

PRIORITIES = (1, 2, 3)


def awaiting_triage():
    """Open visits that have no queue entry yet, oldest first."""
    return (Encounter.objects.filter(status="OPEN", queue_entries__isnull=True)
            .select_related("patient")
            .order_by("opened_at"))


@transaction.atomic
def open_visit(*, patient, encounter_type, payer_type, staff):
    """
    Reception: opens a visit and charges the registration and consultation fees.
    Refused if the patient already has a visit waiting for triage, so a second visit cannot
    be opened by mistake.
    """
    valid_types = dict(Encounter.TYPE_CHOICES)
    if encounter_type not in valid_types:
        raise FlowError("Choose a valid visit type.")
    if Encounter.objects.filter(patient=patient, status="OPEN",
                                queue_entries__isnull=True).exists():
        raise FlowError("This patient already has a visit waiting for triage. "
                        "Triage that visit instead of opening a new one.")

    encounter = Encounter.objects.create(
        patient=patient,
        encounter_type=encounter_type,
        payer_type=payer_type,
        opened_by=staff,
    )
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
    if enc.status != "OPEN":
        raise FlowError("This visit is closed.")
    if enc.queue_entries.exists():
        raise FlowError("This patient has already been triaged.")
    if priority not in PRIORITIES:
        raise FlowError("Choose a valid priority.")
    if doctor is None:
        raise FlowError("Choose a doctor who is on duty.")

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

    TriageVitals.objects.create(encounter=enc, recorded_by=staff, **vitals)

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
