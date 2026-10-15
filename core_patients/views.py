from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Patient, PatientAllergy, Allergen


@login_required
def patient_registry_search(request):
    """
    Live search endpoint triggered via htmx. 
    Requires a minimum query length of 2 characters[cite: 1].
    """
    query = request.GET.get("q", "").strip()
    
    if not query or len(query) < 2:
        if request.headers.get("HX-Request"):
            return render(request, "core_patients/_registry_results.html", {"patients": []})
        return render(request, "core_patients/registry.html")

    patients = Patient.objects.filter(
        Q(last_name__icontains=query) |
        Q(first_name__icontains=query) |
        Q(id_number__icontains=query)
    ).select_related("created_by").order_by("-created_at")[:25]

    context = {"patients": patients, "query": query}

    if request.headers.get("HX-Request"):
        return render(request, "core_patients/_registry_results.html", context)
    return render(request, "core_patients/registry.html", context)


@login_required
def patient_register_view(request):
    """Handles new patient registration and emergency provisional creation."""
    if request.method == "POST":
        is_provisional = request.POST.get("is_provisional") == "true"
        last_name = request.POST.get("last_name", "").strip()
        
        if not is_provisional and not last_name:
            return render(request, "core_patients/register.html", {"error": "Last name is required."})

        patient = Patient.objects.create(
            first_name=request.POST.get("first_name", "").strip(),
            last_name=last_name or "Unidentified",
            date_of_birth=request.POST.get("date_of_birth") or None,
            sex=request.POST.get("sex", "U"),
            id_type=request.POST.get("id_type", "NONE"),
            id_number=request.POST.get("id_number") or None,
            is_provisional=is_provisional,
            provisional_label=request.POST.get("provisional_label", ""),
            phone=request.POST.get("phone", "").strip(),
            created_by=request.staff_profile,
        )
        return redirect("core_patients:detail", patient_id=patient.id)

    return render(request, "core_patients/register.html")


@login_required
def patient_detail_view(request, patient_id):
    patient = get_object_or_404(Patient.objects.select_related("created_by"), pk=patient_id)
    
    patient_allergies = PatientAllergy.objects.filter(
        patient=patient, status="ACTIVE"
    ).select_related("allergen", "allergen__allergen_class")

    timeline_items = []
    for encounter in patient.encounters.all():
        for note in encounter.clinical_notes.all():
            timeline_items.append({
                "type": "NOTE", "get_type_display": "Clinical Note", "timestamp": note.created_at,
                "summary": f"Complaint: {note.presenting_complaint[:80]}... | Diagnosis: {note.diagnosis}", "author": note.author,
            })
        for order in encounter.medication_orders.all():
            timeline_items.append({
                "type": "ORDER", "get_type_display": "Medication Order", "timestamp": order.ordered_at,
                "summary": f"{order.medication.brand_name} {order.dose_text} - {order.get_route_display()}", "author": order.ordered_by,
            })

    timeline_items.sort(key=lambda x: x["timestamp"], reverse=True)
    context = {"patient": patient, "patient_allergies": patient_allergies, "timeline_items": timeline_items}
    return render(request, "core_patients/patient_detail.html", context)

@login_required
def patient_edit_view(request, patient_id):
    """Renders and processes patient demographic edits."""
    patient = get_object_or_404(Patient, pk=patient_id)
    if request.method == "POST":
        patient.first_name = request.POST.get("first_name", "").strip()
        patient.last_name = request.POST.get("last_name", "").strip()
        patient.phone = request.POST.get("phone", "").strip()
        patient.save()
        return redirect("core_patients:detail", patient_id=patient.id)
    return render(request, "core_patients/edit.html", {"patient": patient})


@login_required
def patient_timeline_view(request, patient_id):
    """htmx endpoint for infinite scrolling or refreshing timeline items[cite: 1]."""
    patient = get_object_or_404(Patient, pk=patient_id)
    # Renders timeline fragment
    return render(request, "core_patients/_timeline_items.html", {"patient": patient})


@login_required
def allergy_panel_view(request, patient_id):
    """Renders the allergy management modal or panel[cite: 1]."""
    patient = get_object_or_404(Patient, pk=patient_id)
    allergies = patient.allergies.select_related("allergen").all()
    all_allergens = Allergen.objects.filter(is_active=True)
    context = {"patient": patient, "allergies": allergies, "all_allergens": all_allergens}
    return render(request, "core_patients/_allergy_panel.html", context)


@login_required
@require_POST
def confirm_identity_view(request, patient_id):
    """Clears provisional status after identity verification."""
    if not request.user.staff_profile.role in ("RECEPTION", "ADMIN", "DOCTOR"):
        return HttpResponseForbidden("Not authorized to confirm patient identities.")
    
    patient = get_object_or_404(Patient, pk=patient_id)
    patient.is_provisional = False
    patient.provisional_label = ""
    patient.id_type = request.POST.get("id_type", patient.id_type)
    patient.id_number = request.POST.get("id_number", patient.id_number)
    patient.save()
    return redirect("core_patients:detail", patient_id=patient.id)
