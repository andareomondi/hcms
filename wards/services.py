import math

from django.db import transaction
from django.utils import timezone

from billing import refunds as billing_refunds
from billing import services as billing
from triage_queue.models import QueueEntry
from triage_queue.services import FlowError, finish_encounter

from .models import Admission, Bed, BedStatusLog

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
def admit(*, encounter, bed_id, planned_days, staff):
    if not (1 <= planned_days <= 365):
        raise FlowError("Planned stay must be between 1 and 365 days.")
    if encounter.status != "OPEN":
        raise FlowError("This encounter is closed.")
    if Admission.objects.filter(encounter=encounter, discharged_at__isnull=True).exists():
        raise FlowError("This patient is already admitted.")

    bed = Bed.objects.select_for_update().select_related("ward").filter(pk=bed_id).first()
    if bed is None:
        raise FlowError("Bed not found.")
    if bed.status != VACANT:
        raise FlowError(f"Bed {bed.code} is {bed.get_status_display().lower()}, not vacant.")

    admission = Admission.objects.create(
        encounter=encounter, bed=bed, admitted_at=timezone.now(),
        admitted_by=staff, planned_days=planned_days,
    )
    _record(bed, OCCUPIED, staff, f"Admitted for {planned_days} day(s)", admission)

    billing.add_charge(
        encounter=encounter,
        source_type="BED_DAY",
        description=f"Bed {bed.code} ({bed.ward.name}), planned {planned_days} day(s)",
        unit_price=bed.ward.bed_day_rate,
        quantity=planned_days,
        staff=staff,
        source_id=admission.id,
        key=f"bed:{admission.id}:planned",
    )
    QueueEntry.objects.filter(encounter=encounter).exclude(status="DISCHARGED") \
        .update(status="ADMITTED")
    return admission


@transaction.atomic
def discharge(*, admission, staff):
    adm = Admission.objects.select_for_update().select_related("encounter__patient").get(pk=admission.pk)
    if adm.discharged_at is not None:
        raise FlowError("This patient has already been discharged.")

    now = timezone.now()
    adm.discharged_at = now
    adm.save(update_fields=["discharged_at"])

    bed = Bed.objects.select_for_update().select_related("ward").get(pk=adm.bed_id)
    actual_days = nights_between(adm.admitted_at, now)
    planned_days = adm.planned_days
    rate = bed.ward.bed_day_rate

    # The planned bed-day charge is never voided. Only the difference in days is billed or refunded.
    planned = billing.Charge.objects.filter(idempotency_key=f"bed:{adm.id}:planned").first()

    if actual_days > planned_days:
        billing.add_charge(
            encounter=adm.encounter,
            source_type="BED_DAY",
            description=f"Bed {bed.code} ({bed.ward.name}), {actual_days - planned_days} day(s) beyond planned",
            unit_price=rate,
            quantity=actual_days - planned_days,
            staff=staff,
            source_id=adm.id,
            key=f"bed:{adm.id}:overstay",
        )
    elif actual_days < planned_days:
        billing_refunds.apply_unused_stay(
            encounter=adm.encounter,
            anchor=planned,
            unused=rate * (planned_days - actual_days),
            staff=staff,
            reason=f"Bed {bed.code} left after {actual_days} of {planned_days} planned day(s)",
        )

    _record(bed, CLEANING, staff, "Discharged. Needs cleaning before reuse.", adm)
    finish_encounter(adm.encounter, staff)
    return adm, actual_days
