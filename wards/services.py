import math

from django.db import transaction
from django.utils import timezone

from billing import services as billing
from triage_queue.services import FlowError, finish_encounter

from .models import Admission, Bed


def nights_between(start, end):
    """Bed days are counted from admission. Any part of a day counts as a full day, minimum one."""
    seconds = (end - start).total_seconds()
    return max(1, math.ceil(seconds / 86400))


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
    if bed.status != "VACANT":
        raise FlowError(f"Bed {bed.code} is not vacant.")

    admission = Admission.objects.create(
        encounter=encounter, bed=bed, admitted_at=timezone.now(),
        admitted_by=staff, planned_days=planned_days,
    )
    bed.status = "OCCUPIED"
    bed.save(update_fields=["status"])

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

    from triage_queue.models import QueueEntry
    QueueEntry.objects.filter(encounter=encounter).exclude(status="DISCHARGED") \
        .update(status="ADMITTED")
    return admission


@transaction.atomic
def discharge(*, admission, staff):
    adm = (Admission.objects.select_for_update()
           .select_related("bed__ward", "encounter__patient").get(pk=admission.pk))
    if adm.discharged_at is not None:
        raise FlowError("This patient has already been discharged.")

    now = timezone.now()
    adm.discharged_at = now
    adm.save(update_fields=["discharged_at"])

    actual_days = nights_between(adm.admitted_at, now)
    planned = billing.Charge.objects.filter(idempotency_key=f"bed:{adm.id}:planned").first()
    if planned:
        billing.void_or_reverse(planned, staff)

    bed = adm.bed
    billing.add_charge(
        encounter=adm.encounter,
        source_type="BED_DAY",
        description=f"Bed {bed.code} ({bed.ward.name}), actual {actual_days} day(s)",
        unit_price=bed.ward.bed_day_rate,
        quantity=actual_days,
        staff=staff,
        source_id=adm.id,
        key=f"bed:{adm.id}:final",
    )

    bed.status = "CLEANING"
    bed.save(update_fields=["status"])
    finish_encounter(adm.encounter, staff)
    return adm, actual_days
