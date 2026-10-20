from datetime import datetime

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing import services as billing
from core_patients.models import Patient
from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from hospital_system.permissions import Cap, caps_for, requires
from triage_queue.models import Encounter

from . import services
from .models import Delivery
from .schedule import LABELS, SCHEDULE, schedule_status
from .services import MaternityError


def _profile(request):
    if request.staff_profile is None:
        raise MaternityError("Your account has no staff profile.")
    return request.staff_profile


def _aware(raw):
    raw = (raw or "").strip()
    if not raw:
        raise MaternityError("Enter the date and time.")
    dt = datetime.fromisoformat(raw)
    return timezone.make_aware(dt, timezone.get_current_timezone()) if timezone.is_naive(dt) else dt


def _int_or_none(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise MaternityError("Numbers must be whole numbers.")


@requires(Cap.MATERNITY_RECORD)
def maternity_index_view(request):
    births = Delivery.objects.select_related("mother_encounter__patient", "baby")[:50]
    open_visits = Encounter.objects.filter(status="OPEN").select_related("patient").order_by("-opened_at")[:50]
    return render(request, "maternity/index.html", {"births": births, "open_visits": open_visits})


@requires(Cap.MATERNITY_RECORD)
def delivery_view(request, encounter_id):
    encounter = get_object_or_404(Encounter.objects.select_related("patient"), pk=encounter_id)

    if request.method == "POST":
        try:
            delivery = services.record_delivery(
                mother_encounter=encounter,
                staff=_profile(request),
                delivered_at=_aware(request.POST.get("delivered_at")),
                delivery_type=request.POST.get("delivery_type", ""),
                baby_sex=request.POST.get("baby_sex", "U"),
                birth_weight_g=_int_or_none(request.POST.get("birth_weight_g")),
                apgar_1=_int_or_none(request.POST.get("apgar_1")),
                apgar_5=_int_or_none(request.POST.get("apgar_5")),
                gestation_weeks=_int_or_none(request.POST.get("gestation_weeks")),
                notes=request.POST.get("notes", ""),
                baby_first_name=request.POST.get("baby_first_name", ""),
                baby_last_name=request.POST.get("baby_last_name", ""),
            )
        except MaternityError as exc:
            messages.error(request, str(exc))
            return redirect("maternity:delivery", encounter_id=encounter.id)

        log_action(request, AuditAction.BIRTH_RECORDED,
                   f"Recorded birth for {encounter.patient}: baby {delivery.baby}.")
        messages.success(request, "Birth recorded. The baby has their own record, linked to the mother.")
        return redirect("maternity:child", patient_id=delivery.baby_id)

    return render(request, "maternity/delivery.html", {
        "encounter": encounter,
        "patient": encounter.patient,
        "deliveries": encounter.deliveries.select_related("baby"),
        "delivery_types": Delivery.DELIVERY_TYPES,
        "sexes": Patient.SEX_CHOICES,
        "now_local": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
    })


@requires(Cap.PATIENT_FILE)
def child_view(request, patient_id):
    child = get_object_or_404(Patient, pk=patient_id)
    birth = Delivery.objects.filter(baby=child).select_related("mother_encounter__patient").first()
    rows = schedule_status(child, timezone.localdate())
    caps = caps_for(request.user)
    return render(request, "maternity/child.html", {
        "child": child,
        "birth": birth,
        "schedule": rows,
        "vaccinations": child.vaccinations.select_related("given_by__user").order_by("given_at"),
        "guardians": child.relationships_out.filter(is_active=True).select_related("related_patient"),
        "next_vaccines": [(c, l) for c, l, _ in SCHEDULE],
        "labels": LABELS,
        "can_record": Cap.MATERNITY_RECORD in caps,
        "can_confirm": Cap.PATIENT_REGISTER in caps,
        "held": billing.billing_hold_reason(child),
        "now_local": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
    })


@requires(Cap.MATERNITY_RECORD)
@require_POST
def vaccination_action(request, patient_id):
    child = get_object_or_404(Patient, pk=patient_id)
    try:
        services.record_vaccination(
            patient=child,
            vaccine=request.POST.get("vaccine", ""),
            given_at=_aware(request.POST.get("given_at")),
            serial=request.POST.get("serial", ""),
            staff=_profile(request),
        )
    except MaternityError as exc:
        messages.error(request, str(exc))
        return redirect("maternity:child", patient_id=child.id)

    log_action(request, AuditAction.VACCINATION_RECORDED,
               f"Recorded {request.POST.get('vaccine')} for {child}.")
    messages.success(request, "Vaccination recorded.")
    return redirect("maternity:child", patient_id=child.id)


@requires(Cap.PATIENT_REGISTER)
@require_POST
def confirm_birth_certificate_action(request, patient_id):
    child = get_object_or_404(Patient, pk=patient_id)
    try:
        services.confirm_birth_certificate(
            child=child,
            first_name=request.POST.get("first_name", ""),
            last_name=request.POST.get("last_name", ""),
            certificate_number=request.POST.get("certificate_number", ""),
        )
    except MaternityError as exc:
        messages.error(request, str(exc))
        return redirect("maternity:child", patient_id=child.id)

    log_action(request, AuditAction.BIRTH_CERTIFICATE_CONFIRMED,
               f"Confirmed birth certificate for {child}.")
    messages.success(request, "Birth certificate confirmed. The record is now official.")
    return redirect("maternity:child", patient_id=child.id)
