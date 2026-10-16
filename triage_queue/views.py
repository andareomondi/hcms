from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core_patients.models import Patient, StaffProfile
from .models import Encounter, QueueEntry, TriageVitals


def is_triage_staff(user):
    return hasattr(user, "staff_profile") and user.staff_profile.role in ("TRIAGE_NURSE", "RECEPTION", "DOCTOR", "ADMIN")


@login_required
@user_passes_test(is_triage_staff)
def triage_board_view(request):
    """Renders the wall display or triage nurse work queue board."""
    return render(request, "triage_queue/board.html", _get_board_context())


@login_required
@user_passes_test(is_triage_staff)
def triage_board_fragment(request):
    """htmx poll endpoint returning just the board card container fragment."""
    if not request.headers.get("HX-Request"):
        return HttpResponseBadRequest("Direct access not permitted.")
    return render(request, "triage_queue/board_fragment_content.html", _get_board_context())

@login_required
@user_passes_test(is_triage_staff)
def encounter_create_view(request, patient_id):
    """Opens a new encounter (Outpatient/Emergency) and places the patient on the triage queue."""
    patient = get_object_or_404(Patient, pk=patient_id)

    if request.method == "POST":
        encounter_type = request.POST.get("encounter_type", "OUTPATIENT")
        priority = int(request.POST.get("priority", 1))
        assigned_doctor_id = request.POST.get("assigned_doctor_id")
        
        assigned_doctor = get_object_or_404(StaffProfile, pk=assigned_doctor_id, role="DOCTOR")

        # 1. Create Encounter
        encounter = Encounter.objects.create(
            patient=patient,
            encounter_type=encounter_type,
            payer_type=request.POST.get("payer_type", "SELF_PAY"),
            opened_by=request.staff_profile,
        )

        # 2. Record Triage Vitals if supplied
        temp = request.POST.get("temperature_c")
        pulse = request.POST.get("pulse_bpm")
        sys = request.POST.get("systolic_mmhg")
        dia = request.POST.get("diastolic_mmhg")
        spo2 = request.POST.get("spo2_percent")
        weight = request.POST.get("weight_kg")

        if any([temp, pulse, sys, dia, spo2, weight]):
            TriageVitals.objects.create(
                encounter=encounter,
                recorded_by=request.staff_profile,
                temperature_c=temp or None,
                pulse_bpm=pulse or None,
                systolic_mmhg=sys or None,
                diastolic_mmhg=dia or None,
                spo2_percent=spo2 or None,
                weight_kg=weight or None,
                notes=request.POST.get("notes", "").strip(),
            )

        # 3. Add to Queue
        QueueEntry.objects.create(
            encounter=encounter,
            assigned_doctor=assigned_doctor,
            priority=priority,
            status="WAITING",
        )

        return redirect("triage_queue:board")

    doctors = StaffProfile.objects.filter(role="DOCTOR", is_active=True).select_related("user")
    context = {"patient": patient, "doctors": doctors}
    return render(request, "triage_queue/encounter_form.html", context)


def _get_board_context():
    now = timezone.now()
    active_entries = QueueEntry.objects.exclude(
        status="DISCHARGED"
    ).select_related(
        "encounter__patient", "assigned_doctor__user"
    ).order_by("-priority", "joined_at")

    resuscitation_entries = []
    urgent_entries = []
    routine_entries = []

    total_wait_minutes = 0
    count = 0

    for entry in active_entries:
        wait_delta = now - entry.joined_at
        entry.wait_duration_minutes = int(wait_delta.total_seconds() / 60)
        
        total_wait_minutes += entry.wait_duration_minutes
        count += 1

        if entry.priority == 3:
            resuscitation_entries.append(entry)
        elif entry.priority == 2:
            urgent_entries.append(entry)
        else:
            routine_entries.append(entry)

    avg_wait = int(total_wait_minutes / count) if count > 0 else 0

    return {
        "resuscitation_entries": resuscitation_entries,
        "urgent_entries": urgent_entries,
        "routine_entries": routine_entries,
        "total_waiting_count": count,
        "avg_wait_minutes": avg_wait,
    }
