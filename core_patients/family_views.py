from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing import services as billing
from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from hospital_system.permissions import Cap, caps_for, requires
from maternity.services import MaternityError, link_relationship, register_child
from wards.models import Admission

from .models import Patient, PatientRelationship


def age_label(dob, today):
    if dob is None:
        return "age unknown"
    months = (today.year - dob.year) * 12 + (today.month - dob.month) - (1 if today.day < dob.day else 0)
    if months < 24:
        return f"{max(months, 0)} month(s)"
    return f"{months // 12} year(s)"


@requires(Cap.PATIENT_FILE)
def family_view(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    today = timezone.localdate()
    caps = caps_for(request.user)

    parents = (PatientRelationship.objects.filter(patient=patient, is_active=True)
               .select_related("related_patient").order_by("-is_billing_guarantor", "relationship"))
    children = (PatientRelationship.objects.filter(related_patient=patient, is_active=True)
                .select_related("patient").order_by("patient__date_of_birth"))

    child_rows = []
    for link in children:
        child = link.patient
        child_rows.append({
            "link": link,
            "child": child,
            "age": age_label(child.date_of_birth, today),
            "open_visit": child.encounters.filter(status="OPEN").first(),
            "admitted": Admission.objects.filter(encounter__patient=child, discharged_at__isnull=True)
                        .select_related("bed__ward").first(),
            "held": billing.billing_hold_reason(child) if link.is_billing_guarantor else None,
        })

    account = None
    if Cap.VIEW_FINANCE in caps and hasattr(patient, "billing_account"):
        account = patient.billing_account

    return render(request, "core_patients/family.html", {
        "patient": patient,
        "parents": parents,
        "child_rows": child_rows,
        "patient_age": age_label(patient.date_of_birth, today),
        "account": account,
        "can_register": Cap.PATIENT_REGISTER in caps,
        "relationship_choices": PatientRelationship.RELATIONSHIP_CHOICES,
    })


@requires(Cap.PATIENT_REGISTER)
@require_POST
def register_child_action(request, patient_id):
    parent = get_object_or_404(Patient, pk=patient_id)
    try:
        dob_raw = request.POST.get("date_of_birth", "").strip()
        dob = date.fromisoformat(dob_raw) if dob_raw else None
        child = register_child(
            parent=parent,
            first_name=request.POST.get("first_name", ""),
            last_name=request.POST.get("last_name", ""),
            date_of_birth=dob,
            sex=request.POST.get("sex", "U"),
            id_type=request.POST.get("id_type", "NONE"),
            id_number=request.POST.get("id_number", "").strip(),
            dob_is_estimated=bool(request.POST.get("dob_is_estimated")),
            is_guarantor=bool(request.POST.get("is_guarantor")),
            staff=request.staff_profile,
        )
    except (MaternityError, ValueError) as exc:
        messages.error(request, str(exc) or "Check the child's details.")
        return redirect("core_patients:family", patient_id=parent.id)

    log_action(request, AuditAction.CHILD_REGISTERED,
               f"Registered child {child} under {parent}.")
    messages.success(request, f"{child.first_name} registered and linked to {parent}.")
    return redirect("core_patients:family", patient_id=parent.id)


@requires(Cap.PATIENT_REGISTER)
@require_POST
def link_guardian_action(request, patient_id):
    """
    Links this patient to another existing patient. `role` says which side is the child.
    """
    current = get_object_or_404(Patient, pk=patient_id)
    other = get_object_or_404(Patient, pk=request.POST.get("other_id"))
    role = request.POST.get("role", "")
    if role == "child":
        child, parent = other, current
    elif role == "parent":
        child, parent = current, other
    else:
        messages.error(request, "Say whether the other person is the child or the parent.")
        return redirect("core_patients:family", patient_id=current.id)

    try:
        link_relationship(
            patient=child, related=parent,
            relationship=request.POST.get("relationship", "PARENT"),
            is_guarantor=bool(request.POST.get("is_guarantor")),
        )
    except MaternityError as exc:
        messages.error(request, str(exc))
        return redirect("core_patients:family", patient_id=current.id)

    log_action(request, AuditAction.GUARDIAN_LINKED,
               f"Linked {child} to {parent} as {request.POST.get('relationship', 'PARENT')}.")
    messages.success(request, "Linked.")
    return redirect("core_patients:family", patient_id=current.id)
