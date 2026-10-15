from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from triage_queue.models import Encounter
from .models import Admission, Bed, Ward


def is_ward_staff(user):
    return hasattr(user, "staff_profile") and user.staff_profile.role in ("NURSE", "DOCTOR", "RECEPTION", "ADMIN")


@login_required
@user_passes_test(is_ward_staff)
def bed_grid_view(request):
    wards = Ward.objects.prefetch_related("beds__admissions__encounter__patient").all()
    return render(request, "wards/bed_grid.html", {"wards": wards})


@login_required
@user_passes_test(is_ward_staff)
@transaction.atomic
def admit_patient_view(request, bed_id):
    bed = get_object_or_404(Bed.objects.select_for_update(), pk=bed_id)
    if bed.status != "VACANT":
        return redirect("wards:bed_grid")

    if request.method == "POST":
        encounter = get_object_or_404(Encounter, pk=request.POST.get("encounter_id"), status="OPEN")
        Admission.objects.create(encounter=encounter, bed=bed, admitted_at=timezone.now(), admitted_by=request.staff_profile)
        bed.status = "OCCUPIED"
        bed.save(update_fields=["status"])
        return redirect("wards:bed_grid")

    return render(request, "wards/admit_form.html", {"bed": bed})


@login_required
@user_passes_test(is_ward_staff)
@require_POST
@transaction.atomic
def discharge_patient_view(request, admission_id):
    admission = get_object_or_404(Admission.objects.select_for_update().select_related("bed"), pk=admission_id, discharged_at__isnull=True)
    admission.discharged_at = timezone.now()
    admission.save(update_fields=["discharged_at"])
    
    bed = admission.bed
    bed.status = "CLEANING"
    bed.save(update_fields=["status"])
    return redirect("wards:bed_grid")
