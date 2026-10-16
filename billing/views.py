from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action

from .models import BillingAccount, Charge


@login_required
def billing_dashboard_view(request):
    """Billing & financial dashboard with metrics, search, status filters, and pagination."""
    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()

    # Ensure treated_patient is explicitly selected
    charges_qs = Charge.objects.select_related("treated_patient", "billing_account", "encounter").order_by("-created_at")

    if query:
        charges_qs = charges_qs.filter(
            Q(description__icontains=query) |
            Q(treated_patient__first_name__icontains=query) |
            Q(treated_patient__last_name__icontains=query) |
            Q(treated_patient__id_number__icontains=query)
        )

    if status_filter:
        charges_qs = charges_qs.filter(status=status_filter)

    # Pagination (15 items per page)
    paginator = Paginator(charges_qs, 15)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Metrics Calculations
    total_revenue = Charge.objects.filter(status__in=["POSTED", "PAID"]).aggregate(total=Sum("amount"))["total"] or 0
    provisional_total = Charge.objects.filter(status="PROVISIONAL").aggregate(total=Sum("amount"))["total"] or 0
    total_accounts = BillingAccount.objects.count()
    pending_review_count = Charge.objects.filter(status="PROVISIONAL").count()

    context = {
        "page_obj": page_obj,
        "charges": page_obj.object_list,
        "query": query,
        "status_filter": status_filter,
        "total_revenue": total_revenue,
        "provisional_total": provisional_total,
        "total_accounts": total_accounts,
        "pending_review_count": pending_review_count,
    }
    return render(request, "billing/dashboard.html", context)


@login_required
@require_POST
def post_charge_action(request, charge_id):
    charge = get_object_or_404(Charge, pk=charge_id)
    charge.status = "POSTED"
    charge.save(update_fields=["status"])

    log_action(
        request,
        AuditAction.CHARGE_POSTED,
        f"Posted charge #{charge.id} '{charge.description}' (KES {charge.amount}).",
    )

    messages.success(request, f"Charge '{charge.description}' posted successfully.")
    return redirect("billing:dashboard")


@login_required
@require_POST
def mark_charge_paid_action(request, charge_id):
    """Mark a charge as fully paid."""
    charge = get_object_or_404(Charge, pk=charge_id)
    charge.status = "PAID"
    charge.save(update_fields=["status"])

    log_action(
        request,
        AuditAction.CHARGE_PAID,
        f"Marked charge #{charge.id} '{charge.description}' (KES {charge.amount}) as paid.",
    )

    messages.success(request, f"Charge '{charge.description}' marked as PAID.")
    return redirect("billing:dashboard")
