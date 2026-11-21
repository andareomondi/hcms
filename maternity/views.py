from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing import services as billing
from core_patients.models import Patient
from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from hospital_system.permissions import Cap, caps_for, requires
from pharmacy.models import StockUnit
from triage_queue.models import Encounter

from . import services
from .models import Delivery, DeliveryEpisode, LabourStay, NameCorrection
from .schedule import LABELS, schedule_status
from .services import MaternityError
from wards.models import Bed, Ward
from wards.services import FlowError as WardFlowError


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


def _vials_for_form(today):
    """In-date vials of vaccines, for the vial picker on the child page."""
    units = (StockUnit.objects
             .filter(status="IN_STOCK", doses_remaining__gt=0,
                     medication__vaccine_code__isnull=False,
                     batch__expiry_date__gte=today)
             .select_related("medication", "batch")
             .order_by("batch__expiry_date", "serial"))
    return [
        {
            "code": u.medication.vaccine_code,
            "serial": u.serial,
            "label": (f"{u.serial} · batch {u.batch.batch_number} · "
                      f"exp {u.batch.expiry_date:%d %b %Y} · {u.doses_remaining} dose(s) left"),
        }
        for u in units
    ]


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
    today = timezone.localdate()
    rows = schedule_status(child, today)
    caps = caps_for(request.user)
    return render(request, "maternity/child.html", {
        "child": child,
        "birth": birth,
        "schedule": rows,
        "vaccinations": child.vaccinations.select_related("given_by__user").order_by("given_at"),
        "guardians": child.relationships_out.filter(is_active=True).select_related("related_patient"),
        "labels": LABELS,
        "vials": _vials_for_form(today),
        "can_record": Cap.MATERNITY_RECORD in caps,
        "can_confirm": Cap.PATIENT_REGISTER in caps,
        "held": billing.billing_hold_reason(child),
        "now_local": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
    })


@requires(Cap.MATERNITY_RECORD)
@require_POST
def vaccination_action(request, patient_id):
    child = get_object_or_404(Patient, pk=patient_id)
    # A scanned barcode wins. Otherwise the vial picked from the list is used.
    serial = (request.POST.get("serial_scan") or request.POST.get("serial_pick") or "").strip()
    try:
        services.record_vaccination(
            patient=child,
            vaccine=request.POST.get("vaccine", ""),
            given_at=_aware(request.POST.get("given_at")),
            serial=serial,
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


@requires(Cap.MATERNITY_RECORD)
def temporary_children_view(request):
    from maternity.services import temporary_children
    return render(request, "maternity/temporary_children.html", {"children": temporary_children()})


@requires(Cap.MATERNITY_RECORD)
def after_birth_report_view(request, episode_id):
    from django.http import HttpResponse
    from maternity.models import DeliveryEpisode
    from maternity.report import build_after_birth_report
    episode = get_object_or_404(DeliveryEpisode.objects.select_related(
        "mother_encounter__patient", "recorded_by__user", "signed_off_by__user", "labour_stay__labour_bed"),
        pk=episode_id)
    pdf = build_after_birth_report(episode)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="after-birth-{episode.pk}.pdf"'
    return response


# ---------- labour and delivery screens ----------

def _dec_or_none(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        raise MaternityError(f"'{raw}' is not a number.")


@requires(Cap.MATERNITY_RECORD)
@require_POST
def labour_admit_action(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    try:
        stay = services.open_labour(
            patient=patient, staff=_profile(request),
            delivered_before_arrival=bool(request.POST.get("delivered_before_arrival")),
            arrival_note=request.POST.get("arrival_note", ""),
        )
    except MaternityError as exc:
        messages.error(request, str(exc))
        return redirect("core_patients:detail", patient_id=patient.id)
    log_action(request, AuditAction.LABOUR_ADMITTED,
               f"Admitted {patient} to labour in {stay.labour_bed.code}.")
    messages.success(request, f"Admitted to labour in {stay.labour_bed.code}.")
    return redirect("maternity:labour_record", stay_id=stay.id)


@requires(Cap.MATERNITY_RECORD)
def labour_board_view(request):
    stays = (LabourStay.objects.filter(discharged_at__isnull=True)
             .select_related("patient", "labour_bed").order_by("arrived_at"))
    return render(request, "maternity/labour_board.html", {"stays": stays, "now": timezone.now()})


@requires(Cap.MATERNITY_RECORD)
def labour_record_view(request, stay_id):
    stay = get_object_or_404(LabourStay.objects.select_related("patient", "labour_bed", "encounter"), pk=stay_id)
    episodes = stay.episodes.prefetch_related("babies__baby").order_by("-recorded_at")
    profile = getattr(request, "staff_profile", None)
    return render(request, "maternity/labour_record.html", {
        "stay": stay,
        "episodes": episodes,
        "is_doctor": bool(profile and profile.role == "DOCTOR"),
        "can_record": Cap.MATERNITY_RECORD in caps_for(request.user),
        "now_local": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
    })


def _babies_from_post(post):
    sexes = post.getlist("baby_sex")

    def pick(name, i):
        values = post.getlist(name)
        return values[i] if i < len(values) else ""

    babies = []
    for i in range(len(sexes)):
        babies.append({
            "sex": pick("baby_sex", i) or "U",
            "first_name": pick("baby_first_name", i),
            "last_name": pick("baby_last_name", i),
            "birth_weight_g": _int_or_none(pick("birth_weight_g", i)),
            "apgar_1": _int_or_none(pick("apgar_1", i)),
            "apgar_5": _int_or_none(pick("apgar_5", i)),
            "gestation_weeks": _int_or_none(pick("gestation_weeks", i)),
            "notes": pick("notes", i),
            "heart_rate_bpm": _int_or_none(pick("heart_rate_bpm", i)),
            "respiratory_rate": _int_or_none(pick("respiratory_rate", i)),
            "temperature_c": _dec_or_none(pick("temperature_c", i)),
            "spo2_percent": _int_or_none(pick("spo2_percent", i)),
            "bp_systolic": _int_or_none(pick("bp_systolic", i)),
            "bp_diastolic": _int_or_none(pick("bp_diastolic", i)),
            "birth_length_cm": _dec_or_none(pick("birth_length_cm", i)),
            "head_circumference_cm": _dec_or_none(pick("head_circumference_cm", i)),
        })
    return babies


@requires(Cap.MATERNITY_RECORD)
def labour_birth_view(request, stay_id):
    stay = get_object_or_404(LabourStay.objects.select_related("patient", "encounter"), pk=stay_id)
    if request.method == "POST":
        try:
            father = {
                "first_name": request.POST.get("father_first_name", "").strip(),
                "last_name": request.POST.get("father_last_name", "").strip(),
                "consents": bool(request.POST.get("father_consents")),
            }
            episode = services.record_birth_episode(
                mother_encounter=stay.encounter,
                staff=_profile(request),
                delivered_at=_aware(request.POST.get("delivered_at")),
                delivery_type=request.POST.get("delivery_type", ""),
                babies=_babies_from_post(request.POST),
                father=father,
                complications=request.POST.get("complications", ""),
                delivered_before_arrival=stay.delivered_before_arrival,
            )
        except MaternityError as exc:
            messages.error(request, str(exc))
            return redirect("maternity:labour_birth", stay_id=stay.id)
        log_action(request, AuditAction.BIRTH_RECORDED,
                   f"Recorded birth for {stay.patient}: {episode.babies.count()} baby(ies).")
        messages.success(request, "Birth recorded. Each baby has their own record. Awaiting doctor sign-off.")
        return redirect("maternity:labour_record", stay_id=stay.id)

    return render(request, "maternity/labour_birth.html", {
        "stay": stay,
        "patient": stay.patient,
        "delivery_types": Delivery.DELIVERY_TYPES,
        "sexes": Patient.SEX_CHOICES,
        "now_local": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
    })


@requires(Cap.MATERNITY_RECORD)
@require_POST
def episode_signoff_action(request, episode_id):
    episode = get_object_or_404(DeliveryEpisode.objects.select_related("labour_stay"), pk=episode_id)
    try:
        services.sign_off_episode(episode=episode, doctor=getattr(request, "staff_profile", None),
                                  note=request.POST.get("signoff_note", ""))
    except MaternityError as exc:
        messages.error(request, str(exc))
    else:
        log_action(request, AuditAction.DELIVERY_SIGNED_OFF,
                   f"Signed off delivery of {episode.mother_encounter.patient}.")
        messages.success(request, "Delivery signed off. The after-birth report is now final.")
    if episode.labour_stay_id:
        return redirect("maternity:labour_record", stay_id=episode.labour_stay_id)
    return redirect("maternity:index")


@requires(Cap.MATERNITY_RECORD)
def move_to_ward_view(request, stay_id):
    stay = get_object_or_404(LabourStay.objects.select_related("patient", "labour_bed", "encounter"), pk=stay_id)
    if request.method == "POST":
        try:
            services.move_to_ward(
                encounter=stay.encounter,
                bed_id=request.POST.get("bed_id"),
                planned_days=_int_or_none(request.POST.get("planned_days")) or 1,
                reason=request.POST.get("reason", ""),
                staff=_profile(request),
            )
        except (MaternityError, WardFlowError) as exc:
            messages.error(request, str(exc))
            return redirect("maternity:move_to_ward", stay_id=stay.id)
        log_action(request, AuditAction.MOTHER_MOVED_TO_WARD, f"Moved {stay.patient} to a ward bed.")
        messages.success(request, "Moved to the ward. The ward bed is billed as normal.")
        return redirect("maternity:labour_board")
    return render(request, "maternity/move_to_ward.html", {
        "stay": stay,
        "beds": Bed.objects.filter(status="VACANT").select_related("ward").order_by("ward__name", "code"),
        "is_doctor": bool(getattr(request, "staff_profile", None) and request.staff_profile.role == "DOCTOR"),
    })


@requires(Cap.MATERNITY_RECORD)
def name_correction_request_view(request, patient_id):
    child = get_object_or_404(Patient, pk=patient_id)
    if request.method == "POST":
        try:
            services.request_name_correction(
                patient=child,
                first_name=request.POST.get("first_name", ""),
                last_name=request.POST.get("last_name", ""),
                reason=request.POST.get("reason", ""),
                staff=_profile(request),
            )
        except MaternityError as exc:
            messages.error(request, str(exc))
            return redirect("maternity:name_correction_request", patient_id=child.id)
        log_action(request, AuditAction.NAME_CORRECTION_REQUESTED, f"Requested a name correction for {child}.")
        messages.success(request, "Correction sent. A doctor or system admin must confirm it.")
        return redirect("maternity:name_corrections")
    return render(request, "maternity/name_correction_request.html", {"child": child})


@requires(Cap.MATERNITY_RECORD)
def name_corrections_view(request):
    profile = getattr(request, "staff_profile", None)
    pending = (NameCorrection.objects.filter(status="PENDING")
               .select_related("patient", "requested_by__user").order_by("requested_at"))
    return render(request, "maternity/name_corrections.html", {
        "pending": pending,
        "can_confirm_role": bool(profile and profile.role in ("DOCTOR", "ADMIN")),
        "my_profile_id": profile.pk if profile else None,
    })


@requires(Cap.MATERNITY_RECORD)
@require_POST
def name_correction_confirm_action(request, correction_id):
    correction = get_object_or_404(NameCorrection, pk=correction_id)
    try:
        services.confirm_name_correction(correction=correction, confirmer=_profile(request))
    except MaternityError as exc:
        messages.error(request, str(exc))
    else:
        log_action(request, AuditAction.NAME_CORRECTION_CONFIRMED,
                   f"Confirmed name correction for {correction.patient}.")
        messages.success(request, "Name corrected and confirmed.")
    return redirect("maternity:name_corrections")
