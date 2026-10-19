from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from hospital_system.permissions import Cap, requires

from .consent import (
    ConsentError,
    needs_consent,
    record_consent,
    treatment_consent_status,
)
from .models import ConsentRecord, Patient, PatientRelationship


def _lookup(model, raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return model.objects.filter(pk=raw).first()
    except (ValueError, ValidationError):
        return None


@requires(Cap.CONSENT_RECORD)
def consent_page_view(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    return render(request, "core_patients/consent.html", {
        "patient": patient,
        "status": treatment_consent_status(patient),
        "minor": needs_consent(patient),
        "records": patient.consents.select_related(
            "given_by_patient", "recorded_by__user", "encounter").order_by("-recorded_at"),
        "guardians": PatientRelationship.objects.filter(
            patient=patient, is_active=True).select_related("related_patient"),
        "open_visits": patient.encounters.filter(status="OPEN").order_by("-opened_at"),
        "types": ConsentRecord.TYPES,
    })


@requires(Cap.CONSENT_RECORD)
@require_POST
def consent_record_action(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    try:
        record_consent(
            patient=patient,
            consent_type=request.POST.get("consent_type", ""),
            staff=request.staff_profile,
            given_by_name=request.POST.get("given_by_name", ""),
            given_by_patient=_lookup(Patient, request.POST.get("given_by_patient")),
            relationship=request.POST.get("relationship", ""),
            identification=request.POST.get("identification", ""),
            reason=request.POST.get("reason", ""),
            encounter=_lookup(patient.encounters.model, request.POST.get("encounter")),
        )
    except ConsentError as exc:
        messages.error(request, str(exc))
        return redirect("core_patients:consent", patient_id=patient.id)

    kind = request.POST.get("consent_type", "")
    log_action(request, AuditAction.CONSENT_RECORDED,
               f"Recorded {dict(ConsentRecord.TYPES).get(kind, kind).lower()} for {patient}.")
    messages.success(request, "Consent recorded.")
    return redirect("core_patients:consent", patient_id=patient.id)
