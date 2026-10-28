"""
Billing credit exceptions and the emergency bypass queue (QOL-2).
Lives in its own module. billing/views.py is not changed by these views.
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from hospital_system.permissions import Cap, requires
from lab.models import LabOrder
from pharmacy.models import MedicationOrder
from radiology.models import ScanOrder
from triage_queue.models import Encounter

from . import services as bill


def is_billing_officer(user):
    """The BILLING role only. Admin and superusers are refused by design."""
    profile = getattr(user, "staff_profile", None)
    return profile is not None and profile.role == "BILLING"


@requires(Cap.POST_CHARGES)
@require_POST
def approve_credit_action(request, kind, object_id):
    if not is_billing_officer(request.user):
        raise PermissionDenied
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "Give a reason for the credit exception.")
        return redirect(request.META.get("HTTP_REFERER", "/billing/"))

    if kind == "medication":
        model = MedicationOrder
    elif kind == "scan":
        model = ScanOrder
    elif kind == "lab":
        model = LabOrder
    else:
        raise Http404
    target = get_object_or_404(model, pk=object_id)
    profile = request.staff_profile

    with transaction.atomic():
        target = model.objects.select_for_update().select_related("encounter__patient").get(pk=target.pk)
        if target.credit_approved_by_id:
            messages.info(request, "Credit already approved.")
            return redirect("billing:encounter", encounter_id=target.encounter_id)
        target.credit_approved_by = profile
        target.credit_approved_at = timezone.now()
        target.credit_reason = reason
        target.save(update_fields=["credit_approved_by", "credit_approved_at", "credit_reason"])

    log_action(request, AuditAction.CREDIT_APPROVED,
               f"Credit approved for {kind} #{target.id} for {target.encounter.patient}: {reason}")
    messages.success(request, "Credit exception recorded. The item can now be released.")
    return redirect("billing:encounter", encounter_id=target.encounter_id)


@requires(Cap.VIEW_FINANCE)
def bypass_queue_view(request):
    """Emergency visits treated before payment and not yet cleared by billing."""
    rows = []
    qs = (Encounter.objects.filter(prepay_bypassed=True, prepay_cleared_at__isnull=True)
          .select_related("patient").order_by("prepay_bypass_at"))
    for enc in qs:
        rows.append((enc, bill.encounter_tally(enc)))
    return render(request, "billing/bypass_queue.html", {"rows": rows})
