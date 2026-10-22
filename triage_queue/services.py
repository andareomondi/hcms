from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from billing import services as billing
from billing.models import Charge
from billing.prices import CONSULTATION_FEE, REGISTRATION_FEE
from notifications.services import notify, notify_on_duty
from radiology.models import ScanOrder
from wards.models import Admission

from .models import QueueEntry

PREPAY_FEE_SOURCES = ("REGISTRATION", "CONSULTATION")
OUTSTANDING_SCAN_STATUSES = ("ORDERED", "PERFORMED")


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


def notify_billing_ready_for_bypass(encounter):
    notify_on_duty(
        role="BILLING",
        kind="SYSTEM",
        message=f"Emergency treated before payment: {patient_label(encounter)}.",
        link=reverse("billing:bypass_queue"),
    )


def charge_visit_fees(encounter, staff):
    """Registration (once per patient, by key) and consultation (once per visit, by key)."""
    billing.add_charge(
        encounter=encounter, source_type="REGISTRATION",
        description="Patient registration (one-time)",
        unit_price=REGISTRATION_FEE, staff=staff,
        key=f"reg:{encounter.patient_id}",
    )
    billing.add_charge(
        encounter=encounter, source_type="CONSULTATION",
        description="Consultation fee",
        unit_price=CONSULTATION_FEE, staff=staff,
        key=f"consult:{encounter.id}",
    )


def fees_covered(encounter):
    """True when every pre-pay fee line for this visit is fully paid."""
    fee_lines = (Charge.objects.filter(encounter=encounter, source_type__in=PREPAY_FEE_SOURCES)
                 .exclude(status="VOIDED"))
    return all(billing.charge_fully_paid(c) for c in fee_lines)


@transaction.atomic
def activate_visit(encounter):
    """
    Promote AWAITING_PAYMENT to WAITING once the fees are covered, or at once for an
    emergency-bypassed encounter. Notifies the assigned doctor.
    """
    entry = (QueueEntry.objects.select_for_update()
             .filter(encounter=encounter, status="AWAITING_PAYMENT").first())
    if entry is None:
        return None
    if not (encounter.prepay_bypassed or fees_covered(encounter)):
        return None
    entry.status = "WAITING"
    entry.save(update_fields=["status", "updated_at"])
    notify([entry.assigned_doctor], kind="PATIENT_ASSIGNED",
           message=f"Patient ready: {patient_label(encounter)} (priority {entry.priority}).",
           link=reverse("doctors_portal:dashboard"))
    return entry


def outstanding_scans(encounter):
    return ScanOrder.objects.filter(encounter=encounter, status__in=OUTSTANDING_SCAN_STATUSES)


@transaction.atomic
def pause_for_results(encounter, doctor):
    """
    The doctor sends the patient for a scan. The entry leaves IN_CONSULTATION so the doctor
    is free to take the next patient. Returns the entry, or None if nothing was paused.
    """
    entry = (QueueEntry.objects.select_for_update()
             .filter(encounter=encounter, assigned_doctor=doctor, status="IN_CONSULTATION").first())
    if entry is None:
        return None
    entry.status = "AWAITING_RESULTS"
    entry.save(update_fields=["status", "updated_at"])
    return entry


@transaction.atomic
def resume_consultation(encounter, doctor):
    """
    The doctor returns to a patient who is awaiting results. Only allowed when the doctor
    has no other consultation in progress.
    """
    if QueueEntry.objects.filter(assigned_doctor=doctor, status="IN_CONSULTATION").exists():
        raise FlowError("Finish your current consultation before resuming this one.")
    entry = (QueueEntry.objects.select_for_update()
             .filter(encounter=encounter, assigned_doctor=doctor, status="AWAITING_RESULTS").first())
    if entry is None:
        raise FlowError("This patient is not waiting for results.")
    entry.status = "IN_CONSULTATION"
    entry.save(update_fields=["status", "updated_at"])
    return entry


def notify_results_ready(encounter):
    """Called when the last outstanding scan on a visit is reported. Tells the ordering doctor."""
    if outstanding_scans(encounter).exists():
        return
    entry = (QueueEntry.objects.filter(encounter=encounter, status="AWAITING_RESULTS")
             .select_related("assigned_doctor").first())
    if entry is None:
        return
    notify([entry.assigned_doctor], kind="SYSTEM",
           message=f"Results ready for {patient_label(encounter)}. Resume from your dashboard.",
           link=reverse("doctors_portal:dashboard"))


@transaction.atomic
def close_consultation(encounter, doctor):
    """
    Ends the doctor's consultation. The consultation fee is charged at visit open (QOL-2).
    Blocked while any scan is outstanding (ordered or performed but not reported).
    - If the patient is in a ward, the queue entry moves to ADMITTED and the encounter stays open.
    - Otherwise the queue entry is DISCHARGED, the encounter is CLOSED, and billing is notified.
    """
    entry = (QueueEntry.objects.select_for_update()
             .filter(encounter=encounter, status__in=["IN_CONSULTATION", "ADMITTED"]).first())
    if entry is None:
        raise FlowError("This patient is not in consultation.")
    if entry.assigned_doctor_id != doctor.id:
        raise FlowError("Only the assigned doctor can complete this consultation.")
    if not encounter.clinical_notes.exists():
        raise FlowError("Record a clinical note before completing the consultation.")
    if outstanding_scans(encounter).exists():
        raise FlowError("A scan is still outstanding. Wait for the report, or cancel the scan "
                        "with a reason, before completing the visit.")

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
    elif entry.status == "AWAITING_PAYMENT":
        mode = "AWAITING_PAYMENT"
    elif entry.status == "AWAITING_RESULTS":
        mode = "AWAITING_RESULTS"
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
