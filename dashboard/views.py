from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Count, ProtectedError, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing.models import Charge, Payment
from core_patients.models import Patient, StaffProfile, StaffRegistrationRequest
from duty.services import on_duty_staff
from hospital_system.permissions import caps_for
from pharmacy.models import MedicationOrder
from radiology.models import ScanOrder
from triage_queue.models import Encounter, QueueEntry
from wards.models import Bed, Ward

from .audit_actions import AuditAction
from .models import AuditLog
from .utils import log_action

User = get_user_model()

def is_system_admin(user):
    return user.is_authenticated and user.is_superuser

ROLE_GUIDE = [
    {"role": "Reception", "who": "Front desk and registry",
     "does": "Registers patients, searches the registry, opens visits, runs the triage board, handle ward and bed related tasks, reassigns waiting patients.",
     "sees": "Patient identity, visit dates and types, ward board, who is on duty.",
     "cannot": "Clinical notes, vitals, scans, prescriptions, payments."},
    {"role": "Triage nurse", "who": "Triage",
     "does": "Records vitals and chief complaint, sets priority, assigns an on-duty doctor.",
     "sees": "Triage and clinical data for the patient.",
     "cannot": "Finance, payments, consultations."},
    {"role": "Doctor", "who": "Physicians",
     "does": "Calls patients, writes notes, prescribes, requests scans, admits to a ward, handle ward and bed related tasks, completes consultations.",
     "sees": "Full clinical record, pharmacy records, radiology images and reports.",
     "cannot": "Finance, payments, receipts, reception and triage work."},
    {"role": "Nurse", "who": "Ward and injection nurses",
     "does": "Gives injections and IV medicines, runs the nurse worklist, handle ward and bed related tasks, manages ward bed status.",
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
     "does": "Approves staff accounts, manages data and rooms, reviews the audit log. THEY CAN ALMOST DO EVERYTHING",
     "sees": "Everything, read-only, including finance and clinical records.",
     "cannot": "Consultations, reception, triage, pharmacy or nursing. The admin account is for management, not for doing clinical work."},
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
    """Unified System Administrator Master Dashboard for hospital-wide CRUD and device management with pagination."""
    tab = request.GET.get("tab", "wards")
    
    # Eagerly load wards and beds to optimize performance
    wards = Ward.objects.prefetch_related("beds").order_by("name")
    
    # Paginate staff members (20 per page)
    staff_qs = StaffProfile.objects.select_related("user").order_by("user__username")
    staff_page = Paginator(staff_qs, 30).get_page(request.GET.get("staff_page"))
    
    # Paginate audit logs for the master tab stream (25 per page)
    audit_qs = AuditLog.objects.select_related("actor").filter(
        actor__isnull=False
    ).exclude(actor__username="").order_by("-timestamp")
    audit_page = Paginator(audit_qs, 30).get_page(request.GET.get("audit_page"))

    context = {
        "active_tab": tab,
        "wards": wards,
        "staff_page": staff_page,
        "staff_members": staff_page.object_list,
        "audit_page": audit_page,
        "audit_logs": audit_page.object_list,
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
    """Removes a bed. Only vacant beds with no history can be removed."""
    bed = get_object_or_404(Bed, pk=bed_id)
    code = bed.code
    if bed.status != "VACANT":
        messages.error(request, f"Bed {code} is {bed.get_status_display().lower()}. Only vacant beds can be removed.")
        return redirect("/dashboard/admin-master/?tab=wards")
    try:
        bed.delete()
    except ProtectedError:
        messages.error(request, f"Bed {code} has history and cannot be deleted. Take it out of service instead.")
        return redirect("/dashboard/admin-master/?tab=wards")

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

    paginator = Paginator(logs_qs, 30)
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
    """
    Overview built only from live data. Every number is a real count or sum.
    Each card links to the page where the number can be acted on, and only
    appears for roles allowed to use that page.
    """
    caps = caps_for(request.user)
    profile = getattr(request.user, "staff_profile", None)
    now = timezone.now()
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)

    # Patients and visits (Africa/Nairobi calendar days)
    patients_today = Patient.objects.filter(created_at__date=today).count()
    patients_yesterday = Patient.objects.filter(created_at__date=yesterday).count()
    visits_today = Encounter.objects.filter(opened_at__date=today).count()

    # Waiting queue, with real wait times
    waiting_list = list(
        QueueEntry.objects.filter(status="WAITING")
        .select_related("encounter__patient", "assigned_doctor__user")
        .order_by("-priority", "joined_at")
    )
    for entry in waiting_list:
        entry.wait_minutes = int((now - entry.joined_at).total_seconds() // 60)
    waits = [e.wait_minutes for e in waiting_list]
    avg_wait = round(sum(waits) / len(waits)) if waits else None
    longest_wait = max(waits) if waits else None
    over_30 = sum(1 for w in waits if w >= 30)

    doctors_on_duty = on_duty_staff(role="DOCTOR").count()

    beds = Bed.objects.aggregate(
        total=Count("id"),
        vacant=Count("id", filter=Q(status="VACANT")),
    )

    # Trend against yesterday, only when there is a real figure to compare
    if patients_yesterday:
        change = round((patients_today - patients_yesterday) / patients_yesterday * 100)
        patients_detail = f"{change:+d}% vs yesterday ({patients_yesterday})"
    else:
        patients_detail = "No registrations yesterday to compare"

    # Doctor's own queue
    my_waiting = my_in_consultation = None
    if profile and "CLINICAL_ACTIONS" in caps:
        my_waiting = QueueEntry.objects.filter(assigned_doctor=profile, status="WAITING").count()
        my_in_consultation = QueueEntry.objects.filter(
            assigned_doctor=profile, status="IN_CONSULTATION").count()

    # Pharmacy, nursing and radiology
    nurse_pending = MedicationOrder.objects.filter(status="ORDERED", fulfilment="NURSE_ADMINISTER").count()
    dispense_pending = MedicationOrder.objects.filter(status="ORDERED", fulfilment="PHARMACY_DISPENSE").count()
    scans_pending = ScanOrder.objects.filter(status__in=["ORDERED", "PERFORMED"]).count()

    # Finance
    billed = Charge.objects.exclude(status="VOIDED").aggregate(t=Sum("amount"))["t"] or Decimal(0)
    paid_all = Payment.objects.aggregate(t=Sum("amount"))["t"] or Decimal(0)
    outstanding = billed - paid_all
    payments_today_qs = Payment.objects.filter(recorded_at__date=today)
    payments_today_total = payments_today_qs.aggregate(t=Sum("amount"))["t"] or Decimal(0)
    payments_today_count = payments_today_qs.count()
    provisional_count = Charge.objects.filter(status="PROVISIONAL").count()

    staff_pending = StaffRegistrationRequest.objects.filter(status="PENDING").count()

    # Cards: each has a link and is only added when the role may use that page
    cards = []

    def add(label, value, detail, url, cta, show=True):
        if show:
            cards.append({"label": label, "value": value, "detail": detail, "url": url, "cta": cta})

    add("Patients registered today", patients_today, patients_detail,
        reverse("core_patients:registry"), "Open registry", "PATIENT_SEARCH" in caps)

    add("Visits opened today", visits_today,
        f"{doctors_on_duty} doctor(s) on duty now",
        reverse("triage_queue:board"), "Open triage board", "TRIAGE_BOARD" in caps)

    if "TRIAGE_BOARD" in caps or "CLINICAL_ACTIONS" in caps:
        if waiting_list:
            wait_detail = (f"Average {avg_wait} min · longest {longest_wait} min · "
                           f"{over_30} waiting 30+ min")
        else:
            wait_detail = "No one is waiting"
        waiting_url = (reverse("triage_queue:board") if "TRIAGE_BOARD" in caps
                       else reverse("doctors_portal:dashboard"))
        add("Waiting now", len(waiting_list), wait_detail, waiting_url, "View queue")

    add("Doctors on duty", doctors_on_duty,
        "Clocked in with a room", reverse("duty:board"), "See who is where", True)

    if my_waiting is not None:
        add("My waiting patients", my_waiting,
            f"{my_in_consultation} in consultation now",
            reverse("doctors_portal:dashboard"), "Open my queue")

    add("Beds vacant",
        f"{beds['vacant']} of {beds['total']}" if beds["total"] else "—",
        "No beds configured yet" if not beds["total"] else "Available for admission",
        reverse("wards:dashboard"), "Open bed board", "WARD_VIEW" in caps)

    add("Scans awaiting report", scans_pending,
        "Ordered or performed, not yet reported",
        reverse("radiology:dashboard"), "Open radiology", "VIEW_RADIOLOGY" in caps)

    add("Medicines to give", nurse_pending,
        "Injections and IV orders waiting",
        reverse("pharmacy:nurse_worklist"), "Open nurse worklist", "NURSE_ADMINISTER" in caps)

    add("Medicines to dispense", dispense_pending,
        "Oral orders waiting",
        reverse("pharmacy:dashboard"), "Open pharmacy", "VIEW_PHARMACY" in caps)

    add("Outstanding balance", f"KES {outstanding:,.2f}",
        "Billed minus payments, all visits",
        reverse("billing:bills_ready"), "Open bills", "VIEW_FINANCE" in caps)

    add("Payments today", f"KES {payments_today_total:,.2f}",
        f"{payments_today_count} payment(s) recorded today",
        reverse("billing:bills_ready"), "Open bills", "VIEW_FINANCE" in caps)

    add("Charges to post", provisional_count,
        "Provisional charges awaiting posting",
        reverse("billing:dashboard"), "Open billing", "POST_CHARGES" in caps)

    add("Staff requests pending", staff_pending,
        "New staff accounts awaiting approval",
        reverse("core_patients:staff_requests"), "Review requests", "ADMIN_CONFIG" in caps)

    # Activity: real audit entries only
    if "ADMIN_CONFIG" in caps:
        activity = list(AuditLog.objects.select_related("actor")
                        .filter(actor__isnull=False).order_by("-timestamp")[:8])
        activity_title = "Recent system activity"
    else:
        activity = list(AuditLog.objects.filter(actor=request.user).order_by("-timestamp")[:8])
        activity_title = "Your recent activity"

    hour = timezone.localtime().hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"

    context = {
        "greeting": greeting,
        "today_label": today.strftime("%A, %d %B %Y"),
        "cards": cards,
        "show_waiting_list": "TRIAGE_BOARD" in caps or "CLINICAL_ACTIONS" in caps,
        "waiting_list": waiting_list[:10],
        "waiting_total": len(waiting_list),
        "activity": activity,
        "activity_title": activity_title,
        "show_register": "PATIENT_REGISTER" in caps,
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
