from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from hospital_system.permissions import Cap, caps_for, requires

from .allergy_services import AllergyError, change_allergy_status, record_allergy
from .models import Allergen, Patient, PatientAllergy


def allergy_context(patient, user):
    """Everything the allergy panel needs. Used by the patient file and the panel fragment."""
    return {
        "allergy_rows": (PatientAllergy.objects.filter(patient=patient)
                         .select_related("allergen__allergen_class")
                         .order_by("status", "-recorded_at")),
        "all_allergens": (Allergen.objects.filter(is_active=True)
                          .select_related("allergen_class").order_by("display_name")),
        "can_manage_allergies": Cap.ALLERGY_MANAGE in caps_for(user),
    }


@requires(Cap.PATIENT_FILE)
def allergy_panel_view(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    context = {"patient": patient, **allergy_context(patient, request.user)}
    return render(request, "core_patients/_allergy_panel.html", context)


@requires(Cap.ALLERGY_MANAGE)
@require_POST
def allergy_record_action(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    try:
        allergy, created = record_allergy(
            patient=patient,
            allergen_code=request.POST.get("allergen", ""),
            severity=request.POST.get("severity", ""),
            reaction=request.POST.get("reaction", ""),
            staff=request.staff_profile,
        )
    except AllergyError as exc:
        messages.error(request, str(exc))
        return redirect("core_patients:detail", patient_id=patient.id)

    log_action(request, AuditAction.ALLERGY_RECORDED,
               f"{'Recorded' if created else 'Updated'} allergy to {allergy.allergen.display_name} "
               f"({allergy.get_severity_display()}) for {patient}.")
    messages.success(request, f"Allergy to {allergy.allergen.display_name} saved.")
    return redirect("core_patients:detail", patient_id=patient.id)


@requires(Cap.ALLERGY_MANAGE)
@require_POST
def allergy_status_action(request, patient_id, allergy_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    try:
        allergy = change_allergy_status(
            patient=patient, allergy_id=allergy_id,
            status=request.POST.get("status", ""),
            reason=request.POST.get("reason", ""),
            staff=request.staff_profile,
        )
    except AllergyError as exc:
        messages.error(request, str(exc))
        return redirect("core_patients:detail", patient_id=patient.id)

    log_action(request, AuditAction.ALLERGY_STATUS_CHANGED,
               f"Marked allergy to {allergy.allergen.display_name} {allergy.status.lower()} "
               f"for {patient}: {request.POST.get('reason', '').strip()}")
    messages.success(request, f"Allergy marked {allergy.status.lower()}.")
    return redirect("core_patients:detail", patient_id=patient.id)
