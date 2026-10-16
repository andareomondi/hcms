from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from triage_queue.models import Encounter, QueueEntry

from .models import ClinicalNote


def is_physician(user):
    return hasattr(user, "staff_profile") and user.staff_profile.role in ("DOCTOR", "ADMIN")


@login_required
@user_passes_test(is_physician)
def doctor_dashboard_view(request):
    doctor_profile = request.staff_profile
    now = timezone.now()

    current_consultation = QueueEntry.objects.filter(
        assigned_doctor=doctor_profile,
        status="IN_CONSULTATION"
    ).select_related("encounter__patient", "encounter").first()

    waiting_entries = QueueEntry.objects.filter(
        assigned_doctor=doctor_profile,
        status="WAITING"
    ).select_related("encounter__patient", "encounter").order_by("-priority", "joined_at")

    for entry in waiting_entries:
        entry.wait_duration_minutes = int((now - entry.joined_at).total_seconds() / 60)

    context = {
        "current_consultation": current_consultation,
        "waiting_entries": waiting_entries,
    }
    return render(request, "doctors_portal/dashboard.html", context)


@login_required
@user_passes_test(is_physician)
@require_POST
def call_next_patient_action(request):
    """Marks current consultation as completed/discharged and calls the highest priority waiting patient."""
    doctor_profile = request.staff_profile

    # Finish current active consultation if any
    current = QueueEntry.objects.filter(
        assigned_doctor=doctor_profile,
        status="IN_CONSULTATION"
    ).select_related("encounter__patient").first()

    if current:
        current.status = "DISCHARGED"
        current.save(update_fields=["status"])
        if current.encounter:
            current.encounter.status = "CLOSED"
            current.encounter.save(update_fields=["status"])
            log_action(
                request,
                AuditAction.CONSULTATION_COMPLETED,
                f"Completed consultation for encounter #{current.encounter_id} "
                f"(patient {current.encounter.patient}).",
            )

    # Fetch highest priority waiting patient
    next_entry = QueueEntry.objects.filter(
        assigned_doctor=doctor_profile,
        status="WAITING"
    ).select_related("encounter__patient").order_by("-priority", "joined_at").first()

    if next_entry:
        next_entry.status = "IN_CONSULTATION"
        next_entry.save(update_fields=["status"])
        log_action(
            request,
            AuditAction.QUEUE_NEXT_PATIENT_CALLED,
            f"Called encounter #{next_entry.encounter_id} "
            f"(patient {next_entry.encounter.patient}, priority {next_entry.priority}).",
        )

    return redirect("doctors_portal:dashboard")


@login_required
@user_passes_test(is_physician)
def doctor_workstation_view(request, encounter_id):
    encounter = get_object_or_404(Encounter.objects.select_related("patient"), pk=encounter_id)

    log_action(
        request,
        AuditAction.ENCOUNTER_RECORD_VIEWED,
        f"Opened workstation for encounter #{encounter.id} (patient {encounter.patient}).",
    )

    notes = encounter.clinical_notes.select_related("author__user").all()
    context = {"encounter": encounter, "notes": notes}
    return render(request, "doctors_portal/workstation.html", context)


@login_required
@user_passes_test(is_physician)
@require_POST
def save_clinical_note(request, encounter_id):
    encounter = get_object_or_404(Encounter.objects.select_related("patient"), pk=encounter_id)
    note = ClinicalNote.objects.create(
        encounter=encounter,
        author=request.staff_profile,
        presenting_complaint=request.POST.get("presenting_complaint", "").strip(),
        examination=request.POST.get("examination", "").strip(),
        diagnosis=request.POST.get("diagnosis", "").strip(),
        plan=request.POST.get("plan", "").strip(),
    )

    log_action(
        request,
        AuditAction.CLINICAL_NOTE_SAVED,
        f"Saved clinical note #{note.id} on encounter #{encounter.id} (patient {encounter.patient}).",
    )
    return redirect("doctors_portal:workstation", encounter_id=encounter.id)
