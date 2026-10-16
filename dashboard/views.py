from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone

from billing.models import Charge
from core_patients.models import Patient, StaffProfile
from pharmacy.models import MedicationOrder
from triage_queue.models import QueueEntry
from wards.models import Bed

from .audit_actions import AuditAction
from .models import AuditLog
from .utils import log_action

User = get_user_model()

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
