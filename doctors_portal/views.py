from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from triage_queue.models import Encounter
from .models import ClinicalNote


def is_physician(user):
    return hasattr(user, "staff_profile") and user.staff_profile.role in ("DOCTOR", "ADMIN")


@login_required
@user_passes_test(is_physician)
def doctor_workstation_view(request, encounter_id):
    encounter = get_object_or_404(Encounter.objects.select_related("patient"), pk=encounter_id)
    notes = encounter.clinical_notes.select_related("author__user").all()
    context = {"encounter": encounter, "notes": notes}
    return render(request, "doctors_portal/workstation.html", context)


@login_required
@user_passes_test(is_physician)
@require_POST
def save_clinical_note(request, encounter_id):
    encounter = get_object_or_404(Encounter, pk=encounter_id, status="OPEN")
    ClinicalNote.objects.create(
        encounter=encounter,
        author=request.staff_profile,
        presenting_complaint=request.POST.get("presenting_complaint", "").strip(),
        examination=request.POST.get("examination", "").strip(),
        diagnosis=request.POST.get("diagnosis", "").strip(),
        plan=request.POST.get("plan", "").strip(),
    )
    return redirect("doctors_portal:workstation", encounter_id=encounter.id)
