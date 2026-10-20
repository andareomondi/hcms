import uuid

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from hospital_system.permissions import Cap, requires

from .merge import MergeError, merge_patients
from .models import Patient


@requires(Cap.PATIENT_MERGE)
def merge_page_view(request, patient_id):
    current = get_object_or_404(Patient, pk=patient_id, merged_into__isnull=True)
    return render(request, "core_patients/merge.html", {"current": current})


@requires(Cap.PATIENT_MERGE)
def merge_search_view(request, patient_id):
    current = get_object_or_404(Patient, pk=patient_id, merged_into__isnull=True)
    q = request.GET.get("q", "").strip()
    results = []
    if len(q) >= 2:
        results = (Patient.objects.filter(merged_into__isnull=True)
                   .filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(id_number__icontains=q))
                   .exclude(pk=current.pk).order_by("last_name", "first_name")[:15])
    return render(request, "core_patients/_merge_results.html", {"current": current, "results": results, "q": q})


@requires(Cap.PATIENT_MERGE)
@require_POST
def merge_action(request, patient_id):
    current = get_object_or_404(Patient, pk=patient_id, merged_into__isnull=True)
    try:
        survivor_raw, duplicate_raw = request.POST.get("pair", "").split(":")
        survivor_id, duplicate_id = uuid.UUID(survivor_raw), uuid.UUID(duplicate_raw)
    except ValueError:
        messages.error(request, "Choose which record to keep.")
        return redirect("core_patients:merge", patient_id=current.id)

    if current.pk not in (survivor_id, duplicate_id):
        messages.error(request, "A merge must include the record you opened.")
        return redirect("core_patients:merge", patient_id=current.id)

    survivor = get_object_or_404(Patient, pk=survivor_id)
    duplicate = get_object_or_404(Patient, pk=duplicate_id)
    try:
        merge_patients(survivor=survivor, duplicate=duplicate,
                       reason=request.POST.get("reason", ""), staff=request.staff_profile)
    except MergeError as exc:
        messages.error(request, str(exc))
        return redirect("core_patients:merge", patient_id=current.id)

    messages.success(request, f"Merged {duplicate.first_name} {duplicate.last_name} into {survivor.first_name} {survivor.last_name}.")
    return redirect("core_patients:detail", patient_id=survivor.id)
