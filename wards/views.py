from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from triage_queue.models import Encounter

from .models import Admission, Bed, Ward


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
    bed = get_object_or_404(Bed, pk=bed_id)
    encounter_id = request.POST.get("encounter_id")
    encounter = get_object_or_404(Encounter.objects.select_related("patient"), pk=encounter_id)

    Admission.objects.create(
        encounter=encounter,
        bed=bed,
        admitted_at=timezone.now(),
        admitted_by=getattr(request, "staff_profile", None)
    )

    bed.status = "OCCUPIED"
    bed.save(update_fields=["status"])

    log_action(
        request,
        AuditAction.BED_ASSIGNED,
        f"Assigned bed {bed.code} to patient {encounter.patient} (encounter #{encounter.id}).",
    )

    messages.success(request, f"Successfully admitted {encounter.patient.first_name} to Bed {bed.code}.")
    return redirect("wards:dashboard")


@login_required
@require_POST
def discharge_patient_action(request, admission_id):
    """Discharge a patient from an occupied bed and free the bed."""
    admission = get_object_or_404(
        Admission.objects.select_related("bed", "encounter__patient"),
        pk=admission_id,
        discharged_at__isnull=True,
    )
    admission.discharged_at = timezone.now()
    admission.save(update_fields=["discharged_at"])

    bed = admission.bed
    bed.status = "VACANT"
    bed.save(update_fields=["status"])

    log_action(
        request,
        AuditAction.PATIENT_DISCHARGED,
        f"Discharged patient {admission.encounter.patient} from bed {bed.code} (admission #{admission.id}).",
    )

    messages.success(request, f"Patient discharged from Bed {bed.code}. Bed is now vacant.")
    return redirect("wards:dashboard")
