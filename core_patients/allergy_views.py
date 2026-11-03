from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from hospital_system.permissions import Cap, caps_for

from .models import Allergen, AllergenClass, Patient, PatientAllergyStatement


def allergy_queryset(patient):
    """Every allergy entry for the patient, all statuses, newest first, with its history."""
    return (patient.allergies
            .select_related("allergen__allergen_class", "recorded_by__user")
            .prefetch_related("changes__changed_by__user")
            .order_by("-recorded_at"))


def allergy_context(patient, user):
    """
    Context shared by the patient file, the allergy panel and the consultation room.
    Includes disputed and resolved entries; only ACTIVE entries block medicines (see conflicts_for).
    """
    caps = caps_for(user)
    allergies = allergy_queryset(patient)
    profile = getattr(user, "staff_profile", None)
    return {
        "allergies": allergies,
        "all_allergies": allergies,
        "statement": PatientAllergyStatement.objects.filter(patient=patient).first(),
        "allergens": Allergen.objects.filter(is_active=True).select_related("allergen_class").order_by("display_name"),
        "allergen_classes": AllergenClass.objects.order_by("name"),
        "can_manage_allergies": Cap.ALLERGY_MANAGE in caps,
        "active_count": sum(1 for a in allergies if a.status == "ACTIVE"),
        "is_doctor": bool(profile and profile.role == "DOCTOR"),
    }


@login_required
def allergy_panel_view(request, patient_id):
    """The allergy panel: record new entries and see every entry with its status and history."""
    patient = get_object_or_404(Patient, pk=patient_id)
    log_action(request, AuditAction.PATIENT_ALLERGIES_VIEWED,
               f"Viewed allergy panel for patient #{patient.id}: {patient}.")
    context = {"patient": patient}
    context.update(allergy_context(patient, request.user))
    return render(request, "core_patients/_allergy_panel.html", context)
