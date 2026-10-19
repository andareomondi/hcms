from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from billing import services as billing
from billing.prices import CONSULTATION_FEE
from notifications.services import notify_on_duty
from wards.models import Admission

from .models import QueueEntry


class FlowError(Exception):
    """A user-facing rule violation. The message is shown to staff as-is."""


def patient_label(encounter):
    p = encounter.patient
    return f"{p.last_name}, {p.first_name}".strip(", ")


def notify_billing_ready(encounter):
    total = billing.encounter_tally(encounter)["total"]
    notify_on_duty(
        role="BILLING",
        kind="SYSTEM",
        message=f"Bill ready: {patient_label(encounter)}, KES {total}.",
        link=reverse("billing:encounter", args=[encounter.id]),
    )


def _charge_consultation_fee(encounter, doctor):
    billing.add_charge(
        encounter=encounter,
        source_type="CONSULTATION",
        description=f"Consultation: Dr. {doctor.user.get_full_name() or doctor.user.username}",
        unit_price=CONSULTATION_FEE,
        staff=doctor,
        key=f"consult:{encounter.id}",
    )


@transaction.atomic
def close_consultation(encounter, doctor):
    """
    Ends the doctor's consultation.
    - If the patient is in a ward, the queue entry moves to ADMITTED and the encounter stays open
      until the ward discharge.
    - Otherwise the queue entry is DISCHARGED, the encounter is CLOSED, the consultation fee is
      charged, and billing is notified.
    """
    entry = (QueueEntry.objects.select_for_update()
             .filter(encounter=encounter, status__in=["IN_CONSULTATION", "ADMITTED"]).first())
    if entry is None:
        raise FlowError("This patient is not in consultation.")
    if entry.assigned_doctor_id != doctor.id:
        raise FlowError("Only the assigned doctor can complete this consultation.")
    if not encounter.clinical_notes.exists():
        raise FlowError("Record a clinical note before completing the consultation.")

    _charge_consultation_fee(encounter, doctor)

    if Admission.objects.filter(encounter=encounter, discharged_at__isnull=True).exists():
        entry.status = "ADMITTED"
        entry.save(update_fields=["status", "updated_at"])
        return {"admitted": True}

    entry.status = "DISCHARGED"
    entry.save(update_fields=["status", "updated_at"])
    encounter.status = "CLOSED"
    encounter.closed_at = timezone.now()
    encounter.save(update_fields=["status", "closed_at"])
    notify_billing_ready(encounter)
    return {"admitted": False}


@transaction.atomic
def finish_encounter(encounter, staff):
    """Called when a ward patient is discharged. Closes the queue entry and the encounter."""
    QueueEntry.objects.filter(encounter=encounter).exclude(status="DISCHARGED") \
        .update(status="DISCHARGED")
    if encounter.status != "CLOSED":
        encounter.status = "CLOSED"
        encounter.closed_at = timezone.now()
        encounter.save(update_fields=["status", "closed_at"])
    notify_billing_ready(encounter)

def consultation_mode(encounter, profile):
    """
    Works out what the viewing doctor may do on this visit.
    Returns (mode, queue_entry).
    """
    entry = (QueueEntry.objects.filter(encounter=encounter)
             .select_related("assigned_doctor__user")
             .order_by("-joined_at").first())

    if encounter.status == "CLOSED":
        mode = "CLOSED"
    elif entry is None:
        mode = "NO_QUEUE"
    elif profile is None or entry.assigned_doctor_id != profile.id:
        mode = "NOT_YOURS"
    elif entry.status == "WAITING":
        mode = "WAITING"
    elif entry.status == "IN_CONSULTATION":
        mode = "ACTIVE"
    elif entry.status == "ADMITTED" and encounter.status == "OPEN":
        mode = "IN_WARD"
    else:
        mode = "READ_ONLY"
    return mode, entry


def require_active(encounter, profile, allow_ward=True):
    """
    Raises FlowError unless the doctor may change this visit right now.
    Admission is only allowed while the patient is in consultation (allow_ward=False).
    """
    mode, _ = consultation_mode(encounter, profile)
    allowed = {"ACTIVE", "IN_WARD"} if allow_ward else {"ACTIVE"}
    if mode not in allowed:
        raise FlowError("This consultation is not open for changes.")
    return mode
