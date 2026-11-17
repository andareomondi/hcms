import math
from datetime import timedelta
from secrets import token_urlsafe

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from billing import refunds as billing_refunds
from billing import services as billing_services
from billing.models import Charge
from notifications.services import notify_on_duty
from triage_queue.models import Encounter
from triage_queue.services import FlowError, finish_encounter

from .models import Admission, Bed, BedStatusLog

MIN_REASON_CHARS = 100
CONFIRM_MINUTES = 5

VACANT = "VACANT"
OCCUPIED = "OCCUPIED"
CLEANING = "CLEANING"
ISOLATED = "ISOLATED"
OUT_OF_SERVICE = "OUT_OF_SERVICE"

# Changes made by hand. OCCUPIED is never set by hand: admission sets it and discharge clears it.
TRANSITIONS = {
    VACANT: {ISOLATED, OUT_OF_SERVICE},
    CLEANING: {VACANT, ISOLATED, OUT_OF_SERVICE},   # VACANT here = cleaning signed off
    ISOLATED: {VACANT, OUT_OF_SERVICE},
    OUT_OF_SERVICE: {VACANT, CLEANING},
    OCCUPIED: set(),
}
REASON_REQUIRED = {ISOLATED, OUT_OF_SERVICE}
ACTION_LABELS = {
    VACANT: "Mark ready (vacant)",
    CLEANING: "Start cleaning",
    ISOLATED: "Isolate bed",
    OUT_OF_SERVICE: "Take out of service",
}
ATTENTION_STATUSES = [CLEANING, ISOLATED, OUT_OF_SERVICE]


class DischargeConfirmationRequired(FlowError):
    """The admitting doctor must confirm. Carries a one-time token to send back."""

    def __init__(self, token):
        super().__init__("You admitted this patient. Confirm again to approve their discharge.")
        self.token = token


def available_actions(bed):
    """The buttons a bed card may show, based on its current status."""
    return [
        {"to": to, "label": ACTION_LABELS[to], "needs_reason": to in REASON_REQUIRED}
        for to in sorted(TRANSITIONS.get(bed.status, set()))
    ]


def nights_between(start, end):
    """Bed days are counted from admission. Any part of a day counts as a full day, minimum one."""
    seconds = (end - start).total_seconds()
    return max(1, math.ceil(seconds / 86400))


def _record(bed, to_status, staff, reason="", admission=None):
    """Write the history row and update the bed. Always called inside a transaction."""
    reason = (reason or "")[:255]
    BedStatusLog.objects.create(
        bed=bed,
        from_status=bed.status,
        to_status=to_status,
        reason=reason,
        admission=admission,
        changed_by=staff,
    )
    bed.status = to_status
    bed.status_reason = reason
    bed.status_changed_at = timezone.now()
    bed.save(update_fields=["status", "status_reason", "status_changed_at"])
    return bed


@transaction.atomic
def change_status(*, bed_id, to_status, staff, reason=""):
    """Manual status change: cleaning sign-off, cleaning start, isolation, out of service."""
    bed = Bed.objects.select_for_update().select_related("ward").filter(pk=bed_id).first()
    if bed is None:
        raise FlowError("Bed not found.")

    if to_status not in TRANSITIONS.get(bed.status, set()):
        if to_status == bed.status:
            raise FlowError(f"Bed {bed.code} is already {bed.get_status_display().lower()}.")
        target = dict(Bed.STATUS).get(to_status, to_status)
        raise FlowError(f"Bed {bed.code} cannot go from {bed.get_status_display()} to {target} by hand.")

    reason = (reason or "").strip()
    if to_status in REASON_REQUIRED and not reason:
        raise FlowError("Give a reason for isolating or closing this bed.")

    if Admission.objects.filter(bed=bed, discharged_at__isnull=True).exists():
        raise FlowError("A patient is in this bed. Discharge them first.")

    return _record(bed, to_status, staff, reason)


@transaction.atomic
def admit(*, review, bed_id, planned_days, reason, staff):
    """
    A doctor admits a patient from a consultation visit. Opens the WARD_STAY encounter,
    takes the bed, and posts the planned bed-day charge to the stay.
    The review visit is not changed here.
    """
    if staff is None or staff.role != "DOCTOR":
        raise FlowError("Only a doctor can admit a patient to a ward.")

    reason = (reason or "").strip()
    if len(reason) < MIN_REASON_CHARS:
        raise FlowError(f"Give the reason for admission. It must be at least {MIN_REASON_CHARS} "
                        f"characters ({len(reason)} entered).")
    if not (1 <= planned_days <= 365):
        raise FlowError("Planned stay must be between 1 and 365 days.")
    if review.status != "OPEN":
        raise FlowError("This visit is closed.")
    if review.encounter_type == "WARD_STAY":
        raise FlowError("Admit from a consultation visit, not from a ward stay.")
    if Admission.objects.filter(patient=review.patient, discharged_at__isnull=True).exists():
        raise FlowError("This patient is already admitted.")

    bed = Bed.objects.select_for_update().select_related("ward").filter(pk=bed_id).first()
    if bed is None:
        raise FlowError("Bed not found.")
    if bed.status != VACANT:
        raise FlowError(f"Bed {bed.code} is {bed.get_status_display().lower()}, not vacant.")

    stay = Encounter.objects.create(
        patient=review.patient, encounter_type="WARD_STAY",
        payer_type=review.payer_type, opened_by=staff,
    )
    admission = Admission.objects.create(
        patient=review.patient, encounter=stay, bed=bed,
        admitted_at=timezone.now(), admitted_by=staff,
        planned_days=planned_days, reason=reason,
    )
    _record(bed, OCCUPIED, staff, f"Admitted for {planned_days} day(s)", admission)

    billing_services.add_charge(
        encounter=stay, source_type="BED_DAY",
        description=f"Bed {bed.code} ({bed.ward.name}), planned {planned_days} day(s)",
        unit_price=bed.ward.bed_day_rate, quantity=planned_days, staff=staff,
        source_id=admission.id, key=f"bed:{admission.id}:planned",
    )

    notify_on_duty(role="BILLING", kind="SYSTEM",
                   message=f"Admitted: {review.patient.last_name}, bed {bed.code}. Bed-day charge added.",
                   link=reverse("billing:bills_ready"))
    return admission


@transaction.atomic
def request_discharge(*, admission, staff):
    """Ward staff ask for a discharge. The bed stays occupied until a doctor approves."""
    adm = Admission.objects.select_for_update().select_related("patient", "bed").get(pk=admission.pk)
    if adm.discharged_at is not None:
        raise FlowError("This patient has already been discharged.")
    if adm.discharge_state != "ADMITTED":
        raise FlowError("A discharge has already been requested for this patient.")

    adm.discharge_state = "DISCHARGE_REQUESTED"
    adm.discharge_requested_at = timezone.now()
    adm.discharge_requested_by = staff
    adm.save(update_fields=["discharge_state", "discharge_requested_at", "discharge_requested_by"])

    name = f"{adm.patient.last_name}, {adm.patient.first_name}"
    notify_on_duty(role="DOCTOR", kind="SYSTEM",
                   message=f"Discharge requested for {name} in bed {adm.bed.code}. A doctor must approve.",
                   link=reverse("doctors_portal:dashboard"))
    return adm


@transaction.atomic
def approve_discharge(*, admission, doctor, token=""):
    """
    A doctor approves the discharge.
    - If this doctor admitted the patient, the first call raises DischargeConfirmationRequired
      with a one-time token. The second call must send that token back within CONFIRM_MINUTES.
    - Otherwise the discharge goes through at once.
    """
    if doctor is None or doctor.role != "DOCTOR":
        raise FlowError("Only a doctor can approve a discharge.")

    adm = (Admission.objects.select_for_update()
           .select_related("encounter__patient", "bed__ward", "patient")
           .get(pk=admission.pk))
    if adm.discharged_at is not None:
        raise FlowError("This patient has already been discharged.")
    if adm.discharge_state != "DISCHARGE_REQUESTED":
        raise FlowError("Ward staff must request the discharge first.")

    if adm.admitted_by_id == doctor.id:
        now = timezone.now()
        valid = bool(token and adm.discharge_confirm_token
                     and token == adm.discharge_confirm_token
                     and adm.discharge_confirm_expires
                     and now <= adm.discharge_confirm_expires)
        if not valid:
            new_token = token_urlsafe(24)
            adm.discharge_confirm_token = new_token
            adm.discharge_confirm_expires = now + timedelta(minutes=CONFIRM_MINUTES)
            adm.save(update_fields=["discharge_confirm_token", "discharge_confirm_expires"])
            raise DischargeConfirmationRequired(new_token)
        adm.discharge_confirm_token = ""
        adm.discharge_confirm_expires = None

    outstanding = billing_services.encounter_tally(adm.encounter)["outstanding"]
    if outstanding > 0:
        raise FlowError(f"KES {outstanding} is still owed on this stay. Settle it or get a "
                        "billing credit approval before discharge.")

    adm.discharge_approved_at = timezone.now()
    adm.discharge_approved_by = doctor
    return _finish_discharge(adm, doctor)


def _finish_discharge(adm, staff):
    """Closes the stay: end time, overstay or refund, bed to cleaning, stay encounter closed."""
    now = timezone.now()
    adm.discharged_at = now
    adm.discharge_state = "DISCHARGED"
    adm.save(update_fields=["discharged_at", "discharge_state", "discharge_approved_at",
                            "discharge_approved_by", "discharge_confirm_token",
                            "discharge_confirm_expires"])

    bed = Bed.objects.select_for_update().select_related("ward").get(pk=adm.bed_id)
    actual_days = nights_between(adm.admitted_at, now)
    planned_days = adm.planned_days
    rate = bed.ward.bed_day_rate
    planned = Charge.objects.filter(idempotency_key=f"bed:{adm.id}:planned").first()

    if actual_days > planned_days:
        billing_services.add_charge(
            encounter=adm.encounter, source_type="BED_DAY",
            description=f"Bed {bed.code} ({bed.ward.name}), {actual_days - planned_days} day(s) beyond planned",
            unit_price=rate, quantity=actual_days - planned_days, staff=staff,
            source_id=adm.id, key=f"bed:{adm.id}:overstay",
        )
    elif actual_days < planned_days:
        billing_refunds.apply_unused_stay(
            encounter=adm.encounter, anchor=planned,
            unused=rate * (planned_days - actual_days), staff=staff,
            reason=f"Bed {bed.code} left after {actual_days} of {planned_days} planned day(s)",
        )

    _record(bed, CLEANING, staff, "Discharged. Needs cleaning before reuse.", adm)
    finish_encounter(adm.encounter, staff)
    return adm, actual_days
