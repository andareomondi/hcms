from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core_patients.models import Patient, StaffProfile
from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action

from .models import Encounter, QueueEntry, TriageVitals


def is_triage_staff(user):
    return hasattr(user, "staff_profile") and user.staff_profile.role in ("TRIAGE_NURSE", "RECEPTION", "DOCTOR", "ADMIN")


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
        encounter = Encounter.objects.create(
            patient=patient,
            encounter_type=request.POST.get("encounter_type", "OUTPATIENT"),
            payer_type=request.POST.get("payer_type", "SELF_PAY"),
            opened_by=request.staff_profile,
        )

        temp = request.POST.get("temperature_c")
        pulse = request.POST.get("pulse_bpm")
        if any([temp, pulse]):
            TriageVitals.objects.create(
                encounter=encounter, recorded_by=request.staff_profile,
                temperature_c=temp or None, pulse_bpm=pulse or None,
            )

        assigned_doctor = get_object_or_404(StaffProfile, pk=request.POST.get("assigned_doctor_id"), role="DOCTOR")
        priority = int(request.POST.get("priority", 1))
        QueueEntry.objects.create(encounter=encounter, assigned_doctor=assigned_doctor, priority=priority)

        log_action(
            request,
            AuditAction.ENCOUNTER_OPENED,
            f"Opened encounter #{encounter.id} for {patient} ({encounter.encounter_type}, "
            f"priority {priority}), assigned to {assigned_doctor}.",
        )
        return redirect("triage_queue:board")

    doctors = StaffProfile.objects.filter(role="DOCTOR", is_active=True).select_related("user")
    return render(request, "triage_queue/encounter_form.html", {"patient": patient, "doctors": doctors})


def _get_board_context():
    now = timezone.now()
    active_entries = QueueEntry.objects.exclude(status="DISCHARGED").select_related("encounter__patient", "assigned_doctor__user").order_by("-priority", "joined_at")
    resuscitation_entries, urgent_entries, routine_entries = [], [], []
    for entry in active_entries:
        entry.wait_duration_minutes = int((now - entry.joined_at).total_seconds() / 60)
        if entry.priority == 3:
            resuscitation_entries.append(entry)
        elif entry.priority == 2:
            urgent_entries.append(entry)
        else:
            routine_entries.append(entry)
    return {"resuscitation_entries": resuscitation_entries, "urgent_entries": urgent_entries, "routine_entries": routine_entries}
