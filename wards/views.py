from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from triage_queue.models import Encounter
from triage_queue.services import FlowError

from . import services as ward_services
from .models import Admission, Bed, Ward


@login_required
@require_POST
def update_bed_status_action(request, bed_id):
    """Updates a bed's operational status (e.g. from CLEANING to VACANT/Ready)."""
    bed = get_object_or_404(Bed, pk=bed_id)
    new_status = request.POST.get("status", "VACANT")
    
    valid_statuses = ["VACANT", "CLEANING", "MAINTENANCE", "OCCUPIED"]
    if new_status in valid_statuses:
        bed.status = new_status
        bed.save(update_fields=["status"])
        
        log_action(
            request, 
            AuditAction.BED_ASSIGNED, 
            f"Updated status of Bed {bed.code} ({bed.ward.name}) to {new_status}."
        )
        messages.success(request, f"Bed {bed.code} is now marked as {new_status}.")
    else:
        messages.error(request, "Invalid bed status selected.")
        
    return redirect(request.META.get("HTTP_REFERER", "wards:dashboard"))


@login_required
def ward_dashboard_view(request):
    """Ward & bed management dashboard with occupancy metrics, active admissions, and pagination."""
    ward_qs = Ward.objects.prefetch_related("beds", "department").order_by("name")

    # Pagination (5 wards per page)
    paginator = Paginator(ward_qs, 5)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    total_beds = Bed.objects.count()
    occupied_beds = Bed.objects.filter(status="OCCUPIED").count()
    vacant_beds = Bed.objects.filter(status="VACANT").count()
    cleaning_beds = Bed.objects.filter(status="CLEANING").count()

    occupancy_rate = int((occupied_beds / total_beds) * 100) if total_beds > 0 else 0

    # Fetch active open encounters that are not currently admitted to a bed
    admitted_encounter_ids = Admission.objects.filter(discharged_at__isnull=True).values_list("encounter_id", flat=True)
    available_encounters = Encounter.objects.filter(
        status="OPEN"
    ).exclude(id__in=admitted_encounter_ids).select_related("patient").order_by("-opened_at")[:50]

    # Map active admissions for occupied beds on the current page
    page_beds = [bed for ward in page_obj.object_list for bed in ward.beds.all()]
    active_admissions = {
        adm.bed_id: adm
        for adm in Admission.objects.filter(bed__in=page_beds, discharged_at__isnull=True).select_related("encounter__patient", "admitted_by__user")
    }
    for ward in page_obj.object_list:
        for bed in ward.beds.all():
            bed.active_admission = active_admissions.get(bed.id)

    context = {
        "page_obj": page_obj,
        "wards": page_obj.object_list,
        "total_beds": total_beds,
        "occupied_beds": occupied_beds,
        "vacant_beds": vacant_beds,
        "cleaning_beds": cleaning_beds,
        "occupancy_rate": occupancy_rate,
        "available_encounters": available_encounters,
    }
    return render(request, "wards/dashboard.html", context)


@login_required
@require_POST
def assign_bed_action(request, bed_id):
    encounter = get_object_or_404(Encounter.objects.select_related("patient"), pk=request.POST.get("encounter_id"))
    try:
        days = int(request.POST.get("planned_days", "1"))
        ward_services.admit(encounter=encounter, bed_id=bed_id, planned_days=days,
                            staff=request.staff_profile)
    except (ValueError, FlowError) as exc:
        messages.error(request, str(exc) or "Enter a valid stay length.")
        return redirect("wards:dashboard")

    log_action(request, AuditAction.WARD_ADMISSION_CREATED,
               f"Admitted {encounter.patient} for {days} day(s).")
    messages.success(request, f"{encounter.patient.first_name} admitted for {days} day(s).")
    return redirect("wards:dashboard")


@login_required
@require_POST
def discharge_patient_action(request, admission_id):
    admission = get_object_or_404(Admission, pk=admission_id, discharged_at__isnull=True)
    try:
        _, actual = ward_services.discharge(admission=admission, staff=request.staff_profile)
    except FlowError as exc:
        messages.error(request, str(exc))
        return redirect("wards:dashboard")

    log_action(request, AuditAction.PATIENT_DISCHARGED,
               f"Discharged {admission.encounter.patient} after {actual} day(s).")
    messages.success(request, f"Discharged after {actual} day(s). The bill is ready for billing.")
    return redirect("wards:dashboard")
