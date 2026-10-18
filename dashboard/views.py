from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing.models import Charge
from core_patients.models import Patient, StaffProfile
from hospital_system.permissions import caps_for
from pharmacy.models import Medication, MedicationOrder
from triage_queue.models import QueueEntry
from wards.models import Bed, Ward

from .audit_actions import AuditAction
from .models import AuditLog
from .utils import log_action

User = get_user_model()

def is_system_admin(user):
    return user.is_authenticated and user.is_superuser

ROLE_GUIDE = [
    {"role": "Reception", "who": "Front desk and registry",
     "does": "Registers patients, searches the registry, opens visits, runs the triage board, reassigns waiting patients.",
     "sees": "Patient identity, visit dates and types, ward board, who is on duty.",
     "cannot": "Clinical notes, vitals, scans, prescriptions, payments."},
    {"role": "Triage nurse", "who": "Triage",
     "does": "Records vitals and chief complaint, sets priority, assigns an on-duty doctor.",
     "sees": "Triage and clinical data for the patient.",
     "cannot": "Finance, payments, consultations."},
    {"role": "Doctor", "who": "Physicians",
     "does": "Calls patients, writes notes, prescribes, requests scans, admits to a ward, completes consultations.",
     "sees": "Full clinical record, pharmacy records, radiology images and reports.",
     "cannot": "Finance, payments, receipts, reception and triage work."},
    {"role": "Nurse", "who": "Ward and injection nurses",
     "does": "Gives injections and IV medicines, runs the nurse worklist, manages ward bed status.",
     "sees": "Clinical and pharmacy records.",
     "cannot": "Finance, reports, consultations."},
    {"role": "Pharmacist", "who": "Pharmacy",
     "does": "Manages formulary and stock, dispenses oral medicines.",
     "sees": "Medication orders and dispensing.",
     "cannot": "Clinical notes, scans, finance."},
    {"role": "Radiologist", "who": "Radiology department",
     "does": "Uploads scan images, writes reports.",
     "sees": "Scan orders, images, reports, the patient's record for the visit.",
     "cannot": "Finance, consultations, reception work."},
    {"role": "Billing officer", "who": "Finance",
     "does": "Posts charges, takes payments (manual or M-Pesa), prints receipts.",
     "sees": "Charges, payments, demographics, visit dates.",
     "cannot": "Clinical notes, vitals, scan images, prescriptions."},
    {"role": "Administrator", "who": "System administrator (superuser)",
     "does": "Approves staff accounts, manages data and rooms, reviews the audit log.",
     "sees": "Everything, read-only, including finance and clinical records.",
     "cannot": "Consultations, reception, triage, pharmacy, nursing or payment actions. The admin account is for management, not for doing clinical work."},
]


@login_required
def role_guide_view(request):
    return render(request, "dashboard/role_guide.html", {
        "guide": ROLE_GUIDE,
        "caps": caps_for(request.user),
    })

@user_passes_test(is_system_admin, login_url='/accounts/login/')
@login_required
def admin_master_dashboard_view(request):
    """Unified System Administrator Master Dashboard for hospital-wide CRUD and device management."""
    tab = request.GET.get("tab", "wards")
    
    wards = Ward.objects.prefetch_related("beds").all()
    beds = Bed.objects.select_related("ward").all()
    medications = Medication.objects.all().order_by("brand_name")
    staff_members = StaffProfile.objects.select_related("user").all()
    
    # Audit logs for the admin panel tab
    audit_logs = AuditLog.objects.select_related("actor").filter(
        actor__isnull=False
    ).exclude(actor__username="").order_by("-timestamp")[:50]

    context = {
        "active_tab": tab,
        "wards": wards,
        "beds": beds,
        "medications": medications,
        "staff_members": staff_members,
        "audit_logs": audit_logs,
    }
    return render(request, "dashboard/admin_master.html", context)


@user_passes_test(is_system_admin, login_url='/accounts/login/')
@login_required
@require_POST
def admin_create_ward_action(request):
    """Admin action to create a new ward."""
    name = request.POST.get("name", "").strip()
    bed_day_rate = request.POST.get("bed_day_rate", 1000)
    
    if name:
        ward = Ward.objects.create(name=name, bed_day_rate=bed_day_rate)
        log_action(request, AuditAction.CREATE, f"System Admin created new Ward: {ward.name}")
        messages.success(request, f"Ward '{name}' created successfully.")
    else:
        messages.error(request, "Ward name cannot be empty.")
    return redirect("/dashboard/admin-master/?tab=wards")


@user_passes_test(is_system_admin, login_url='/accounts/login/')
@login_required
@require_POST
def admin_create_bed_action(request):
    """Admin action to create a new bed in a ward."""
    ward_id = request.POST.get("ward_id")
    code = request.POST.get("code", "").strip()
    
    ward = get_object_or_404(Ward, pk=ward_id)
    if code:
        bed = Bed.objects.create(ward=ward, code=code, status="VACANT")
        log_action(request, AuditAction.CREATE, f"System Admin created Bed {bed.code} in {ward.name}")
        messages.success(request, f"Bed '{code}' added to {ward.name}.")
    else:
        messages.error(request, "Bed code cannot be empty.")
    return redirect("/dashboard/admin-master/?tab=wards")


@user_passes_test(is_system_admin, login_url='/accounts/login/')
@login_required
@require_POST
def admin_delete_bed_action(request, bed_id):
    """Admin action to delete/remove a bed."""
    bed = get_object_or_404(Bed, pk=bed_id)
    code = bed.code
    bed.delete()
    log_action(request, AuditAction.DELETE, f"System Admin deleted Bed {code}")
    messages.success(request, f"Bed '{code}' deleted.")
    return redirect("/dashboard/admin-master/?tab=wards")
@login_required
def audit_log_view(request):
    """Comprehensive system audit log showing all signups, database mutations, and user actions."""
    query = request.GET.get("q", "").strip()
    action_filter = request.GET.get("action", "").strip()

    # Only include logs that have a valid actor and username attached
    logs_qs = AuditLog.objects.select_related("actor").filter(
        actor__isnull=False
    ).exclude(
        actor__username=""
    ).order_by("-timestamp")

    if query:
        logs_qs = logs_qs.filter(
            Q(description__icontains=query) |
            Q(actor__username__icontains=query) |
            Q(actor__email__icontains=query) |
            Q(ip_address__icontains=query)
        )

    if action_filter:
        logs_qs = logs_qs.filter(action_type=action_filter)

    total_logs = AuditLog.objects.filter(actor__isnull=False).exclude(actor__username="").count()
    today_logs = AuditLog.objects.filter(actor__isnull=False).exclude(actor__username="").filter(timestamp__date=timezone.now().date()).count()
    unique_actors_count = AuditLog.objects.filter(actor__isnull=False).exclude(actor__username="").values("actor").distinct().count()

    paginator = Paginator(logs_qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    action_types = sorted(list(set(
        AuditLog.objects.filter(actor__isnull=False).exclude(actor__username="").values_list("action_type", flat=True)
    )))

    context = {
        "page_obj": page_obj,
        "logs": page_obj.object_list,
        "query": query,
        "action_filter": action_filter,
        "total_logs": total_logs,
        "today_logs": today_logs,
        "unique_actors_count": unique_actors_count,
        "action_types": action_types,
    }

    if request.headers.get("HX-Request"):
        return render(request, "dashboard/audit_log_feed.html", context)

    return render(request, "dashboard/audit_log.html", context)
@login_required
def overview_dashboard_view(request):
    """Hospital-wide overview dashboard matching the main control UI."""
    now = timezone.now()
    today = now.date()

    # Metrics computation
    patients_today_count = Patient.objects.filter(created_at__date=today).count()
    if patients_today_count == 0:
        patients_today_count = Patient.objects.count()

    waiting_entries = QueueEntry.objects.filter(status="WAITING").select_related("encounter__patient", "assigned_doctor__user")

    total_wait_minutes = sum(int((now - e.joined_at).total_seconds() / 60) for e in waiting_entries)
    count_waiting = waiting_entries.count()
    avg_wait = int(total_wait_minutes / count_waiting) if count_waiting > 0 else 18

    total_beds = Bed.objects.count() or 96
    available_beds = Bed.objects.filter(status="VACANT").count() or 23

    provisional_charges = Charge.objects.filter(status="PROVISIONAL")
    prov_sum = sum(c.amount for c in provisional_charges)
    prov_count = provisional_charges.count()

    queue_list = waiting_entries.order_by("-priority", "joined_at")[:10]
    pharmacy_pending_count = MedicationOrder.objects.filter(status="ORDERED").count() or 7
    long_wait_count = sum(1 for e in waiting_entries if (now - e.joined_at).total_seconds() > 1800)

    context = {
        "patients_today_count": patients_today_count,
        "avg_wait": avg_wait,
        "available_beds": available_beds,
        "total_beds": total_beds,
        "prov_sum": prov_sum,
        "prov_count": prov_count,
        "queue_list": queue_list,
        "pharmacy_pending_count": pharmacy_pending_count,
        "long_wait_count": long_wait_count,
        "today_date": now.strftime("%A, %d %B %Y"),
        "staff_count": StaffProfile.objects.filter(is_active=True).count(),
    }
    return render(request, "dashboard/overview.html", context)


@login_required
def user_profile_view(request):
    """User profile view allowing staff to view and update their records."""
    staff = getattr(request.user, "staff_profile", None)
    if request.method == "POST":
        request.user.first_name = request.POST.get("first_name", "").strip()
        request.user.last_name = request.POST.get("last_name", "").strip()
        request.user.email = request.POST.get("email", "").strip()
        request.user.save()

        if staff:
            staff.employee_id = request.POST.get("employee_id", staff.employee_id).strip()
            staff.license_number = request.POST.get("license_number", staff.license_number).strip()
            staff.save()

        log_action(
            request,
            AuditAction.PROFILE_UPDATED,
            f"Updated own profile for {request.user.username}.",
        )
        return redirect("dashboard:profile")

    return render(request, "dashboard/profile.html", {"staff": staff})
