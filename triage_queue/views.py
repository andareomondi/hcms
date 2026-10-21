from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core_patients.models import Patient
from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from duty.services import on_duty_staff
from notifications.services import notify

from .models import Encounter, QueueEntry, TriageVitals
from .services import charge_visit_fees, notify_billing_ready_for_bypass


def is_triage_staff(user):
    return hasattr(user, "staff_profile") and user.staff_profile.role in ("TRIAGE_NURSE", "RECEPTION", "DOCTOR", "ADMIN")


@login_required
@user_passes_test(is_triage_staff)
@require_POST
def reassign_entry_action(request, entry_id):
    entry = get_object_or_404(QueueEntry.objects.select_related("encounter__patient", "assigned_doctor__user"),
                              pk=entry_id, status="WAITING")
    new_doctor = on_duty_staff(role="DOCTOR").filter(pk=request.POST.get("doctor_id")).first()
    if new_doctor is None:
        messages.error(request, "Choose a doctor who is on duty.")
        return redirect("triage_queue:board")

    old_doctor = entry.assigned_doctor
    with transaction.atomic():
        entry.assigned_doctor = new_doctor
        entry.save(update_fields=["assigned_doctor", "updated_at"])
        patient = entry.encounter.patient
        notify([new_doctor], kind="PATIENT_ASSIGNED",
               message=f"Reassigned to you: {patient.last_name}, {patient.first_name}.",
               link=reverse("doctors_portal:dashboard"))
        if old_doctor and old_doctor.pk != new_doctor.pk:
            notify([old_doctor], kind="SYSTEM",
                   message=f"{patient.last_name}, {patient.first_name} was reassigned to another doctor.")

    log_action(request, AuditAction.ENCOUNTER_OPENED,
               f"Reassigned encounter #{entry.encounter_id} to {new_doctor}.")
    messages.success(request, f"Reassigned to {new_doctor}.")
    return redirect("triage_queue:board")


@login_required
@user_passes_test(is_triage_staff)
def triage_board_view(request):
    context = _get_board_context()
    if request.headers.get("HX-Request"):
        return render(request, "triage_queue/_board_fragment.html", context)
    return render(request, "triage_queue/board.html", context)


@login_required
@user_passes_test(is_triage_staff)
def triage_board_fragment(request):
    if not request.headers.get("HX-Request"):
        return HttpResponseBadRequest("Direct access not permitted.")
    return render(request, "triage_queue/_board_fragment.html", _get_board_context())


@login_required
@user_passes_test(is_triage_staff)
def encounter_create_view(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)

    if request.method == "POST":
        doctor_id = request.POST.get("assigned_doctor_id")
        doctor = on_duty_staff(role="DOCTOR").filter(pk=doctor_id).first() if doctor_id else None
        if doctor is None:
            messages.error(request, "Choose a doctor who is currently on duty.")
            return redirect("triage_queue:encounter_create", patient_id=patient.id)

        def _num(name, cast=str):
            raw = request.POST.get(name, "").strip()
            return cast(raw) if raw else None

        priority = int(request.POST.get("priority", 1))
        encounter_type = request.POST.get("encounter_type", "OUTPATIENT")
        emergency = priority == 3 or encounter_type == "EMERGENCY"
        bypass_reason = request.POST.get("prepay_bypass_reason", "").strip()
        if emergency and not bypass_reason:
            messages.error(request, "An emergency is treated before payment. Give the reason.")
            return redirect("triage_queue:encounter_create", patient_id=patient.id)

        with transaction.atomic():
            encounter = Encounter.objects.create(
                patient=patient,
                encounter_type=encounter_type,
                payer_type=request.POST.get("payer_type", "SELF_PAY"),
                opened_by=request.staff_profile,
                chief_complaint=request.POST.get("chief_complaint", "").strip(),
                prepay_bypassed=emergency,
                prepay_bypass_reason=bypass_reason if emergency else "",
                prepay_bypass_by=request.staff_profile if emergency else None,
                prepay_bypass_at=timezone.now() if emergency else None,
            )
            TriageVitals.objects.create(
                encounter=encounter,
                recorded_by=request.staff_profile,
                temperature_c=_num("temperature_c"),
                pulse_bpm=_num("pulse_bpm", int),
                systolic_mmhg=_num("systolic_mmhg", int),
                diastolic_mmhg=_num("diastolic_mmhg", int),
                respiratory_rate=_num("respiratory_rate", int),
                spo2_percent=_num("spo2_percent", int),
                weight_kg=_num("weight_kg"),
                notes=request.POST.get("triage_notes", "").strip(),
            )
            charge_visit_fees(encounter, request.staff_profile)
            QueueEntry.objects.create(
                encounter=encounter, assigned_doctor=doctor, priority=priority,
                status="WAITING" if emergency else "AWAITING_PAYMENT",
            )

        log_action(request, AuditAction.ENCOUNTER_OPENED,
                   f"Opened encounter #{encounter.id} for {patient} ({encounter.encounter_type}, "
                   f"priority {priority}), assigned to {doctor}.")
        if emergency:
            log_action(request, AuditAction.PREPAY_BYPASSED,
                       f"Emergency bypass for {patient} on encounter #{encounter.id}: {bypass_reason}")
            notify_billing_ready_for_bypass(encounter)
            notify([doctor], kind="PATIENT_ASSIGNED",
                   message=f"Emergency: {patient.last_name}, {patient.first_name} (priority {priority}).",
                   link=reverse("doctors_portal:dashboard"))
            messages.success(request, "Emergency patient queued. Treated before payment; billing will be asked to clear it.")
        else:
            messages.success(request, "Visit opened. The patient pays the fees at the till before joining the queue.")
        return redirect("triage_queue:board")

    doctors = on_duty_staff(role="DOCTOR").select_related("user").order_by("user__last_name")
    return render(request, "triage_queue/encounter_form.html", {"patient": patient, "doctors": doctors})


def _get_board_context():
    now = timezone.now()
    active_entries = QueueEntry.objects.filter(status="WAITING").select_related(
        "encounter__patient", "assigned_doctor__user").order_by("-priority", "joined_at")
    resuscitation_entries, urgent_entries, routine_entries = [], [], []
    for entry in active_entries:
        entry.wait_duration_minutes = int((now - entry.joined_at).total_seconds() / 60)
        if entry.priority == 3:
            resuscitation_entries.append(entry)
        elif entry.priority == 2:
            urgent_entries.append(entry)
        else:
            routine_entries.append(entry)

    return {
        "resuscitation_entries": resuscitation_entries,
        "urgent_entries": urgent_entries,
        "routine_entries": routine_entries,
        "awaiting_payment": QueueEntry.objects.filter(status="AWAITING_PAYMENT").count(),
    }
