from django.contrib import messages
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core_patients.models import Patient
from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from duty.services import on_duty_staff
from hospital_system.permissions import Cap, requires
from notifications.services import notify

from .intake import awaiting_triage, open_visit, triage_encounter
from .models import Encounter, QueueEntry
from .services import FlowError, fees_covered

# ---------- reception: open the visit ----------

@requires(Cap.OPEN_ENCOUNTER)
def open_visit_view(request, patient_id):
    """Reception opens a visit for a registered patient and charges the fees."""
    patient = get_object_or_404(Patient, pk=patient_id)

    if request.method == "POST":
        try:
            encounter = open_visit(
                patient=patient,
                encounter_type=request.POST.get("encounter_type", "OUTPATIENT"),
                payer_type=request.POST.get("payer_type", "SELF_PAY"),
                staff=request.staff_profile,
            )
        except FlowError as exc:
            messages.error(request, str(exc))
            return redirect("triage_queue:encounter_create", patient_id=patient.id)

        log_action(request, AuditAction.ENCOUNTER_OPENED,
                   f"Opened visit #{encounter.id} for {patient} ({encounter.encounter_type}).")
        messages.success(request, "Visit opened. The fees are due at the till. "
                                  "The patient goes to triage once the visit is in the triage list.")
        return redirect("triage_queue:board")

    return render(request, "triage_queue/open_visit_form.html", {"patient": patient})


# ---------- triage nurse: triage the visit ----------

@requires(Cap.TRIAGE_RECORD)
def triage_visit_view(request, encounter_id):
    encounter = get_object_or_404(Encounter.objects.select_related("patient"), pk=encounter_id)
    doctors = on_duty_staff(role="DOCTOR").select_related("user").order_by("user__last_name")

    if request.method == "POST":
        def _num(name, cast=str):
            raw = request.POST.get(name, "").strip()
            return cast(raw) if raw else None

        try:
            priority = int(request.POST.get("priority", 1))
        except ValueError:
            messages.error(request, "Choose a valid priority.")
            return redirect("triage_queue:triage_visit", encounter_id=encounter.id)

        doctor_id = request.POST.get("assigned_doctor_id")
        doctor = doctors.filter(pk=doctor_id).first() if doctor_id else None

        vitals = dict(
            temperature_c=_num("temperature_c"),
            pulse_bpm=_num("pulse_bpm", int),
            systolic_mmhg=_num("systolic_mmhg", int),
            diastolic_mmhg=_num("diastolic_mmhg", int),
            respiratory_rate=_num("respiratory_rate", int),
            spo2_percent=_num("spo2_percent", int),
            weight_kg=_num("weight_kg"),
            notes=request.POST.get("triage_notes", "").strip(),
        )
        try:
            enc, entry, emergency = triage_encounter(
                encounter=encounter,
                staff=request.staff_profile,
                doctor=doctor,
                priority=priority,
                vitals=vitals,
                chief_complaint=request.POST.get("chief_complaint", ""),
                bypass_reason=request.POST.get("prepay_bypass_reason", ""),
            )
        except (FlowError, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect("triage_queue:triage_visit", encounter_id=encounter.id)

        log_action(request, AuditAction.ENCOUNTER_OPENED,
                   f"Triaged visit #{enc.id} for {enc.patient} (priority {priority}), "
                   f"assigned to {doctor}.")
        if emergency:
            log_action(request, AuditAction.PREPAY_BYPASSED,
                       f"Emergency bypass for {enc.patient} on visit #{enc.id}: {enc.prepay_bypass_reason}")
            messages.success(request, "Emergency patient queued. Treated before payment; billing will clear it.")
        elif entry.status == "WAITING":
            messages.success(request, "Triaged. Fees are paid, so the patient is queued for the doctor.")
        else:
            messages.success(request, "Triaged. The patient joins the doctor's queue once the fees are paid.")
        return redirect("triage_queue:board")

    return render(request, "triage_queue/triage_form.html", {
        "encounter": encounter,
        "patient": encounter.patient,
        "doctors": doctors,
        "fees_paid": fees_covered(encounter),
    })


# ---------- board ----------

@requires(Cap.TRIAGE_BOARD)
def triage_board_view(request):
    context = _get_board_context(request)
    if request.headers.get("HX-Request"):
        return render(request, "triage_queue/_board_fragment.html", context)
    return render(request, "triage_queue/board.html", context)


@requires(Cap.TRIAGE_BOARD)
def triage_board_fragment(request):
    if not request.headers.get("HX-Request"):
        return HttpResponseBadRequest("Direct access not permitted.")
    return render(request, "triage_queue/_board_fragment.html", _get_board_context(request))


@requires(Cap.TRIAGE_BOARD)
@require_POST
def reassign_entry_action(request, entry_id):
    entry = get_object_or_404(QueueEntry.objects.select_related("encounter__patient", "assigned_doctor__user"),
                              pk=entry_id, status="WAITING")
    new_doctor = on_duty_staff(role="DOCTOR").filter(pk=request.POST.get("doctor_id")).first()
    if new_doctor is None:
        messages.error(request, "Choose a doctor who is on duty.")
        return redirect("triage_queue:board")

    old_doctor = entry.assigned_doctor
    entry.assigned_doctor = new_doctor
    entry.save(update_fields=["assigned_doctor", "updated_at"])
    patient = entry.encounter.patient
    notify([new_doctor], kind="PATIENT_ASSIGNED",
           message=f"Reassigned to you: {patient.last_name}, {patient.first_name}.",
           link=reverse("doctors_portal:dashboard"))
    if old_doctor and old_doctor.pk != new_doctor.pk:
        notify([old_doctor], kind="SYSTEM",
               message=f"{patient.last_name}, {patient.first_name} was reassigned to another doctor.")
    log_action(request, AuditAction.ENCOUNTER_REASSIGNED,
               f"Reassigned visit #{entry.encounter_id} to {new_doctor}.")
    messages.success(request, f"Reassigned to {new_doctor}.")
    return redirect("triage_queue:board")


def _get_board_context(request):
    from django.utils import timezone as tz
    now = tz.now()
    active_entries = (QueueEntry.objects.filter(status="WAITING")
                      .select_related("encounter__patient", "assigned_doctor__user")
                      .order_by("-priority", "joined_at"))
    resuscitation, urgent, routine = [], [], []
    for entry in active_entries:
        entry.wait_duration_minutes = int((now - entry.joined_at).total_seconds() / 60)
        if entry.priority == 3:
            resuscitation.append(entry)
        elif entry.priority == 2:
            urgent.append(entry)
        else:
            routine.append(entry)

    triage_list = list(awaiting_triage()[:50])
    for enc in triage_list:
        enc.fees_paid = fees_covered(enc)

    # Count untriaged visits with unpaid fees + queue entries awaiting payment
    untriaged_awaiting_payment = sum(1 for enc in triage_list if not enc.fees_paid)
    queue_awaiting_payment = QueueEntry.objects.filter(status="AWAITING_PAYMENT").count()
    total_awaiting_payment = untriaged_awaiting_payment + queue_awaiting_payment

    return {
        "resuscitation_entries": resuscitation,
        "urgent_entries": urgent,
        "routine_entries": routine,
        "awaiting_triage": triage_list,
        "awaiting_payment": total_awaiting_payment,
        "can_open_visit": request.user.is_superuser or _has(request, Cap.OPEN_ENCOUNTER),
        "can_triage": _has(request, Cap.TRIAGE_RECORD),
    }


def _has(request, cap):
    from hospital_system.permissions import caps_for
    return cap in caps_for(request.user)
