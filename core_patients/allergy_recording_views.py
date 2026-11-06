from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from hospital_system.permissions import Cap, requires

from .allergy_recording import (
    AllergyError,
    add_allergen,
    change_allergy_status,
    record_allergy,
)
from .models import Allergen, AllergenClass, Patient, PatientAllergy


def _entries_from_post(request):
    """
    Reads repeated allergy rows: allergen_id, severity, reaction, source. Rows where the allergen
    is blank are ignored. A new allergen typed by staff is added to the catalogue first.
    """
    ids = request.POST.getlist("allergen_id")
    sev = request.POST.getlist("severity")
    rea = request.POST.getlist("reaction")
    src = request.POST.getlist("source")
    new_names = request.POST.getlist("new_allergen_name")
    new_classes = request.POST.getlist("new_allergen_class")
    rows = []
    for i in range(len(ids)):
        allergen_id = (ids[i] or "").strip()
        new_name = (new_names[i] if i < len(new_names) else "").strip()
        if not allergen_id and not new_name:
            continue
        rows.append({
            "allergen_id": allergen_id,
            "new_name": new_name,
            "new_class": (new_classes[i] if i < len(new_classes) else "").strip(),
            "severity": (sev[i] if i < len(sev) else "").strip(),
            "reaction": (rea[i] if i < len(rea) else "").strip(),
            "source": (src[i] if i < len(src) else "").strip(),
        })
    return rows


def save_allergy_rows(request, patient, rows):
    """Records each row. Returns the number recorded. Raises AllergyError on the first bad row."""
    saved = 0
    for row in rows:
        if row["allergen_id"]:
            allergen = Allergen.objects.filter(pk=row["allergen_id"], is_active=True).first()
            if allergen is None:
                raise AllergyError("One of the allergens is not in the catalogue.")
        else:
            klass = AllergenClass.objects.filter(pk=row["new_class"]).first()
            allergen = add_allergen(display_name=row["new_name"], allergen_class=klass,
                                    staff=request.staff_profile)
        entry = record_allergy(patient=patient, allergen=allergen, severity=row["severity"],
                               reaction=row["reaction"], source=row["source"],
                               staff=request.staff_profile)
        log_action(request, AuditAction.ALLERGY_RECORDED,
                   f"Recorded {allergen.display_name} ({entry.get_severity_display()}) for {patient}.")
        saved += 1
    return saved


@requires(Cap.ALLERGY_MANAGE)
@require_POST
def allergy_record_action(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    try:
        saved = save_allergy_rows(request, patient, _entries_from_post(request))
    except AllergyError as exc:
        messages.error(request, str(exc))
        return redirect("core_patients:detail", patient_id=patient.id)
    if saved == 0:
        messages.error(request, "Add at least one allergy, or choose None known.")
    else:
        messages.success(request, f"{saved} allergy record(s) saved.")
    return redirect("core_patients:detail", patient_id=patient.id)


@requires(Cap.ALLERGY_MANAGE)
@require_POST
def allergy_status_action(request, patient_id, allergy_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    allergy = get_object_or_404(PatientAllergy, pk=allergy_id, patient=patient)
    try:
        change_allergy_status(allergy=allergy, to_status=request.POST.get("to_status", ""),
                              reason=request.POST.get("reason", ""), staff=request.staff_profile)
    except AllergyError as exc:
        messages.error(request, str(exc))
    else:
        log_action(request, AuditAction.ALLERGY_STATUS_CHANGED,
                   f"{allergy.allergen.display_name} for {patient} changed to {allergy.get_status_display()}.")
        messages.success(request, "Allergy status updated. The change is kept in the history.")
    return redirect("core_patients:detail", patient_id=patient.id)
