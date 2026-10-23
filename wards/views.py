from datetime import date, datetime, time, timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from hospital_system.permissions import Cap, caps_for, requires
from triage_queue.models import Encounter
from triage_queue.services import FlowError

from . import services
from .models import Admission, Bed, Ward


def _back(request):
    """Return to the page the user came from, but only if it is on this site."""
    target = request.META.get("HTTP_REFERER")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return reverse("wards:dashboard")


@requires(Cap.WARD_VIEW)
def ward_dashboard_view(request):
    caps = caps_for(request.user)
    can_manage = Cap.BED_MANAGE in caps
    can_admit = Cap.WARD_ACTIONS in caps

    ward_qs = (Ward.objects.select_related("department")
               .prefetch_related(Prefetch("beds", queryset=Bed.objects.order_by("code")))
               .order_by("name"))
    page_obj = Paginator(ward_qs, 5).get_page(request.GET.get("page"))

    status_counts = {code: Bed.objects.filter(status=code).count() for code, _ in Bed.STATUS}
    total_beds = sum(status_counts.values())
    occupancy_rate = round(status_counts["OCCUPIED"] / total_beds * 100) if total_beds else 0

    active = {a.bed_id: a for a in
              Admission.objects.filter(discharged_at__isnull=True)
              .select_related("encounter__patient", "bed")}
    for ward in page_obj.object_list:
        for bed in ward.beds.all():
            bed.active_admission = active.get(bed.id)
            bed.actions = services.available_actions(bed) if can_manage and not bed.active_admission else []

    attention = list(Bed.objects.filter(status__in=services.ATTENTION_STATUSES)
                     .select_related("ward").order_by("status", "ward__name", "code"))
    for bed in attention:
        bed.actions = services.available_actions(bed) if can_manage else []

    available_encounters = (Encounter.objects.filter(status="OPEN")
                            .exclude(admissions__discharged_at__isnull=True)
                            .select_related("patient").order_by("-opened_at")[:50])

    return render(request, "wards/dashboard.html", {
        "page_obj": page_obj,
        "wards": page_obj.object_list,
        "status_counts": status_counts,
        "status_choices": Bed.STATUS,
        "total_beds": total_beds,
        "occupancy_rate": occupancy_rate,
        "attention": attention,
        "available_encounters": available_encounters,
        "can_manage": can_manage,
        "can_admit": can_admit,
    })


@requires(Cap.WARD_ACTIONS)
@require_POST
def assign_bed_action(request, bed_id):
    encounter = get_object_or_404(Encounter.objects.select_related("patient"),
                                  pk=request.POST.get("encounter_id"))
    try:
        days = int(request.POST.get("planned_days", "1"))
        services.admit(encounter=encounter, bed_id=bed_id, planned_days=days,
                       staff=request.staff_profile)
    except (ValueError, FlowError) as exc:
        messages.error(request, str(exc) or "Enter a valid stay length.")
        return redirect("wards:dashboard")

    log_action(request, AuditAction.WARD_ADMISSION_CREATED,
               f"Admitted {encounter.patient} for {days} day(s).")
    messages.success(request, f"{encounter.patient.first_name} admitted for {days} day(s).")
    return redirect("wards:dashboard")


@requires(Cap.WARD_ACTIONS)
@require_POST
def discharge_patient_action(request, admission_id):
    admission = get_object_or_404(Admission, pk=admission_id, discharged_at__isnull=True)
    try:
        _, actual = services.discharge(admission=admission, staff=request.staff_profile)
    except FlowError as exc:
        messages.error(request, str(exc))
        return redirect("wards:dashboard")

    log_action(request, AuditAction.PATIENT_DISCHARGED,
               f"Discharged {admission.encounter.patient} after {actual} day(s).")
    messages.success(request, f"Discharged after {actual} day(s). The bed now needs cleaning.")
    return redirect("wards:dashboard")


@requires(Cap.BED_MANAGE)
@require_POST
def change_bed_status_action(request, bed_id):
    try:
        bed = services.change_status(
            bed_id=bed_id,
            to_status=request.POST.get("status", ""),
            staff=request.staff_profile,
            reason=request.POST.get("reason", ""),
        )
    except FlowError as exc:
        messages.error(request, str(exc))
        return redirect(_back(request))

    log_action(request, AuditAction.BED_STATUS_CHANGED,
               f"Bed {bed.code} ({bed.ward.name}) set to {bed.get_status_display()}."
               + (f" Reason: {bed.status_reason}" if bed.status_reason else ""))
    messages.success(request, f"Bed {bed.code} is now {bed.get_status_display().lower()}.")
    return redirect(_back(request))



@requires(Cap.WARD_VIEW)
def bed_history_view(request, bed_id):
    bed = get_object_or_404(Bed.objects.select_related("ward"), pk=bed_id)
    logs = (bed.status_logs.select_related("changed_by__user", "admission__encounter__patient")
            .order_by("-changed_at")[:200])
    stays = (Admission.objects.filter(bed=bed)
             .select_related("encounter__patient", "admitted_by__user", "encounter__opened_by__user")
             .order_by("-admitted_at"))
    return render(request, "wards/bed_history.html", {"bed": bed, "logs": logs, "stays": stays})

@requires(Cap.WARD_VIEW)
def ward_occupancy_view(request):
    """Who has been in each bed, newest stay first. Filter by ward and date."""
    ward_id = request.GET.get("ward", "")
    day_raw = request.GET.get("date", "").strip()

    stays = (Admission.objects.select_related(
                 "bed__ward", "encounter__patient", "admitted_by__user", "encounter__opened_by__user")
             .order_by("-admitted_at"))

    if ward_id:
        stays = stays.filter(bed__ward_id=ward_id)

    chosen_day = None
    if day_raw:
        try:
            chosen_day = date.fromisoformat(day_raw)
        except ValueError:
            messages.error(request, "Use a date in the format YYYY-MM-DD.")
        else:
            # A stay covers the day if it started before the day ended and
            # ended after the day started (or is still open).
            day_start = timezone.make_aware(datetime.combine(chosen_day, time.min))
            day_end = day_start + timedelta(days=1)
            stays = stays.filter(admitted_at__lt=day_end).filter(
                Q(discharged_at__isnull=True) | Q(discharged_at__gt=day_start))

    page_obj = Paginator(stays, 30).get_page(request.GET.get("page"))
    return render(request, "wards/occupancy.html", {
        "page_obj": page_obj,
        "wards": Ward.objects.order_by("name"),
        "selected_ward": ward_id,
        "day": day_raw,
        "chosen_day": chosen_day,
    })
