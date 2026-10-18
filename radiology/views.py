import hashlib
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from hospital_system.permissions import Cap, requires

from .models import ImagingStudy, ScanOrder


def is_radiologist(user):
    return hasattr(user, "staff_profile") and user.staff_profile.role in ("RADIOLOGIST", "DOCTOR", "ADMIN")


@requires(Cap.VIEW_RADIOLOGY)
def study_file_view(request, study_id):
    """Serves a scan file only to radiology-authorised users. Every view is logged."""
    study = get_object_or_404(ImagingStudy.objects.select_related("order__encounter__patient"), pk=study_id)
    if not study.file:
        raise Http404("No file attached.")
    log_action(request, AuditAction.RADIOLOGY_REPORT_SAVED,
               f"Viewed scan file for {study.order.encounter.patient} ({study.order.modality}).")
    return FileResponse(study.file.open("rb"), filename=os.path.basename(study.file.name))

@login_required
@user_passes_test(is_radiologist)
def radiology_dashboard_view(request):
    """Radiology technician and doctor imaging dashboard & worklist with search and pagination."""
    query = request.GET.get("q", "").strip()
    page_number = request.GET.get("page", 1)

    # Base querysets
    pending_qs = ScanOrder.objects.filter(status__in=["ORDERED", "PERFORMED"]).select_related(
        "encounter__patient", "ordered_by__user"
    ).order_by("-ordered_at")

    completed_qs = ImagingStudy.objects.filter(order__status="REPORTED").select_related(
        "order__encounter__patient", "reported_by__user"
    ).order_by("-reported_at")

    # Filter if search query exists
    if query:
        pending_qs = pending_qs.filter(
            Q(encounter__patient__first_name__icontains=query) |
            Q(encounter__patient__last_name__icontains=query) |
            Q(modality__icontains=query) |
            Q(body_part__icontains=query) |
            Q(clinical_indication__icontains=query)
        )
        completed_qs = completed_qs.filter(
            Q(order__encounter__patient__first_name__icontains=query) |
            Q(order__encounter__patient__last_name__icontains=query) |
            Q(order__modality__icontains=query) |
            Q(order__body_part__icontains=query) |
            Q(report__icontains=query)
        )

    # Paginate pending orders (15 per page)
    pending_paginator = Paginator(pending_qs, 15)
    pending_page = pending_paginator.get_page(page_number)

    # Paginate completed studies (15 per page)
    completed_paginator = Paginator(completed_qs, 15)
    completed_page = completed_paginator.get_page(page_number)

    context = {
        "pending_orders": pending_page,
        "completed_studies": completed_page,
        "total_pending": ScanOrder.objects.filter(status__in=["ORDERED", "PERFORMED"]).count(),
        "total_completed": ImagingStudy.objects.count(),
        "query": query,
    }

    # If it's an HTMX request, render only the partial list tables
    if request.headers.get("HX-Request"):
        return render(request, "radiology/_worklist_tables.html", context)

    return render(request, "radiology/dashboard.html", context)

@login_required
@user_passes_test(is_radiologist)
@require_POST
def save_radiology_report(request, order_id):
    """Uploads imaging study file, computes SHA256 checksum, saves report, and generates billing charge."""
    scan_order = get_object_or_404(ScanOrder.objects.select_related("encounter__patient"), pk=order_id)
    
    report_text = request.POST.get("report", "").strip()
    scan_file = request.FILES.get("file")
    
    if report_text:
        file_sha256 = ""
        if scan_file:
            sha256_hash = hashlib.sha256()
            for chunk in scan_file.chunks():
                sha256_hash.update(chunk)
            file_sha256 = sha256_hash.hexdigest()

        ImagingStudy.objects.create(
            order=scan_order,
            file=scan_file,
            file_sha256=file_sha256,
            uploaded_by=request.user.staff_profile,
            report=report_text,
            reported_by=request.user.staff_profile,
            reported_at=timezone.now()
        )
        
        scan_order.status = "REPORTED"
        scan_order.save(update_fields=["status"])
        
        log_action(request, AuditAction.RADIOLOGY_REPORT_SAVED, f"Saved radiology report for patient ({scan_order.modality}).")
        messages.success(request, "Radiology report successfully saved for patient.")
    else:
        messages.error(request, "Report findings are required.")
        
    return redirect("radiology:dashboard")
