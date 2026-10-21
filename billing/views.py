from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from hospital_system.permissions import Cap, requires
from triage_queue.models import Encounter

from . import payments
from . import services as bill
from .models import BillingAccount, Charge, Payment
from .payments import PaymentError


def is_billing(user):
    profile = getattr(user, "staff_profile", None)
    return user.is_superuser or (profile is not None and profile.role in ("BILLING", "ADMIN"))

@requires(Cap.VIEW_FINANCE)
def account_statement_view(request, account_id):
    account = get_object_or_404(BillingAccount.objects.select_related("owner"), pk=account_id)

    charges = Charge.objects.filter(billing_account=account).exclude(status="VOIDED")
    encounter_ids = charges.values_list("encounter_id", flat=True).distinct()
    encounters = (Encounter.objects.filter(pk__in=encounter_ids)
                  .select_related("patient").order_by("-opened_at"))
    visits = [{"encounter": e, "tally": bill.encounter_tally(e)} for e in encounters]

    payments = (Payment.objects.filter(billing_account=account)
                .select_related("encounter__patient", "recorded_by__user").order_by("-recorded_at"))

    total = sum((row["tally"]["total"] for row in visits), Decimal(0))
    paid = sum((p.amount for p in payments), Decimal(0))
    totals = {"billed": total, "paid": paid, "outstanding": total - paid}

    people = {e.patient_id: e.patient for e in encounters}
    people[account.owner_id] = account.owner

    return render(request, "billing/account.html", {
        "account": account, "visits": visits, "payments": payments,
        "totals": totals, "people": list(people.values()),
    })


@requires(Cap.TAKE_PAYMENT)
@require_POST
def take_payment_action(request, encounter_id):
    encounter = get_object_or_404(Encounter, pk=encounter_id)
    if request.staff_profile is None:
        messages.error(request, "Your account has no staff profile, so payments cannot be recorded.")
        return redirect("billing:encounter", encounter_id=encounter.id)
    try:
        payment, cleared_now = payments.take_payment(
            encounter=encounter,
            method=request.POST.get("method", ""),
            amount_raw=request.POST.get("amount", ""),
            reference=request.POST.get("reference", ""),
            payer_phone=request.POST.get("payer_phone", ""),
            staff=request.staff_profile,
        )
    except PaymentError as exc:
        messages.error(request, str(exc))
        return redirect("billing:encounter", encounter_id=encounter.id)

    log_action(request, AuditAction.PAYMENT_RECORDED,
               f"Recorded {payment.get_method_display()} payment KES {payment.amount} "
               f"({payment.receipt_number}) for {encounter.patient}.")
    if cleared_now:
        log_action(request, AuditAction.PREPAY_BYPASS_CLEARED,
                   f"Emergency visit #{encounter.id} cleared by payment {payment.receipt_number}.")
    messages.success(request, f"Payment recorded. Receipt {payment.receipt_number}.")
    return redirect("billing:payment_receipt", payment_id=payment.id)


@requires(Cap.VIEW_FINANCE)
def payment_receipt_view(request, payment_id):
    payment = get_object_or_404(
        Payment.objects.select_related("encounter__patient", "recorded_by__user"), pk=payment_id)
    return render(request, "billing/receipt.html", {
        "payment": payment,
        "encounter": payment.encounter,
        "tally": bill.encounter_tally(payment.encounter),
    })

@requires(Cap.VIEW_FINANCE)
def bills_ready_view(request):
    candidates = (Encounter.objects.filter(charges__isnull=False)
                  .select_related("patient").distinct().order_by("-opened_at")[:200])
    rows = []
    for enc in candidates:
        tally = bill.encounter_tally(enc)
        if tally["outstanding"] > 0 or tally["paid"] > 0:
            rows.append((enc, tally))
    return render(request, "billing/bills_ready.html", {"rows": rows})

@login_required
@user_passes_test(is_billing)
def encounter_bill_view(request, encounter_id):
    encounter = get_object_or_404(Encounter.objects.select_related("patient"), pk=encounter_id)
    charges = Charge.objects.filter(encounter=encounter).order_by("created_at")
    payments = (Payment.objects.filter(encounter=encounter)
                .select_related("recorded_by__user").order_by("-recorded_at"))
    return render(request, "billing/encounter_bill.html", {
        "encounter": encounter,
        "charges": charges,
        "tally": bill.encounter_tally(encounter),
        "payments": payments,
        "hold": bill.billing_hold_reason(encounter.patient),
    })

@login_required
@user_passes_test(is_billing)
@require_POST
def post_encounter_bill_action(request, encounter_id):
    encounter = get_object_or_404(Encounter, pk=encounter_id)
    if encounter.status != "CLOSED":
        messages.error(request, "Only closed visits can be posted. Finish the consultation or discharge first.")
        return redirect("billing:encounter", encounter_id=encounter.id)
    if request.staff_profile is None:
        messages.error(request, "Your account has no staff profile.")
        return redirect("billing:encounter", encounter_id=encounter.id)

    count = bill.post_encounter_charges(encounter, request.staff_profile)
    log_action(request, AuditAction.CHARGE_POSTED,
               f"Posted {count} charge(s) for {encounter.patient} (encounter #{encounter.id}).")
    messages.success(request, f"Posted {count} charge(s).")
    return redirect("billing:encounter", encounter_id=encounter.id)

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
