from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from billing.services import lab_charge_or_none  # noqa: F401  (used by services)
from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from hospital_system.permissions import Cap, caps_for, requires
from triage_queue.models import Encounter
from triage_queue.services import FlowError, require_active

from . import services as lab
from .models import LabOrder, LabOrderLine
from .services import LabError


def _profile_or_none(request):
    return getattr(request, "staff_profile", None)


def _entrant_ids(order):
    """Staff who touched results or the report on this order. They may not verify it."""
    ids = {l.entered_by_id for l in order.lines.all() if l.entered_by_id}
    if order.report_by_id:
        ids.add(order.report_by_id)
    return ids


def _back_to_dashboard(tab="to_enter"):
    from django.urls import reverse
    return redirect(f"{reverse('lab:dashboard')}?tab={tab}")


# ---------- ordering (doctor) ----------

@requires(Cap.LAB_ORDER)
@require_POST
def order_tests_action(request, encounter_id):
    encounter = get_object_or_404(Encounter, pk=encounter_id)
    profile = _profile_or_none(request)
    if profile is None or profile.role != "DOCTOR":
        messages.error(request, "Only the assigned doctor can order laboratory tests.")
        return redirect("doctors_portal:dashboard")
    try:
        require_active(encounter, profile)
        order, paused = lab.order_tests(
            encounter=encounter, doctor=profile,
            test_ids=request.POST.getlist("test_ids"),
            indication=request.POST.get("indication", ""),
            staff=profile,
        )
    except (LabError, FlowError) as exc:
        messages.error(request, str(exc))
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)

    log_action(request, AuditAction.LAB_ORDER_PLACED,
               f"Lab order for {encounter.patient}: {order.lines.count()} test(s).")
    messages.success(request, "Tests sent to the lab. The patient pays each test at billing before it is processed.")
    if paused:
        return redirect("doctors_portal:dashboard")
    return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)


# ---------- lab dashboard ----------

@requires(Cap.LAB_VIEW)
def lab_dashboard_view(request):
    tab = request.GET.get("tab", "to_receive")
    caps = caps_for(request.user)
    profile = _profile_or_none(request)

    base_lines = (LabOrderLine.objects.exclude(order__status="CANCELLED")
                  .select_related("order__encounter__patient", "order__ordered_by__user", "test"))
    base_orders = (LabOrder.objects.select_related("encounter__patient", "ordered_by__user")
                   .prefetch_related("lines__test", "lines__entered_by"))

    # Counts drive the tab labels. Payment is checked per line, so the counts are computed in Python.
    unpaid_lines = [l for l in base_lines.filter(status="ORDERED") if not lab.line_prepaid(l)]
    receivable = [o for o in base_orders.filter(status__in=["ORDERED", "RECEIVED"])
                  if any(lab.line_prepaid(l) for l in o.lines.filter(status="ORDERED"))]
    to_enter = list(base_orders.filter(status__in=["RECEIVED", "RESULTED"])
                    .filter(lines__status="RECEIVED").distinct())
    to_verify = list(base_orders.filter(status="RESULTED").exclude(report=""))
    released = list(base_orders.filter(status="RELEASED").order_by("-released_at")[:200])

    counts = {
        "awaiting_payment": len(unpaid_lines),
        "to_receive": len(receivable),
        "to_enter": len(to_enter),
        "to_verify": len(to_verify),
        "released": len(released),
    }

    items = {
        "awaiting_payment": unpaid_lines,
        "to_receive": receivable,
        "to_enter": to_enter,
        "to_verify": to_verify,
        "released": released,
    }.get(tab, [])

    if tab == "to_verify" and profile is not None:
        for o in items:
            o.viewer_is_entrant = profile.id in _entrant_ids(o)

    return render(request, "lab/dashboard.html", {
        "tab": tab,
        "counts": counts,
        "items": items,
        "can_receive": Cap.LAB_RECEIVE in caps,
        "can_enter": Cap.LAB_ENTER_RESULT in caps,
        "can_verify": Cap.LAB_VERIFY in caps,
        "can_amend": Cap.LAB_AMEND in caps,
    })


# ---------- receipt of the sample ----------

@requires(Cap.LAB_RECEIVE)
@require_POST
def receive_order_action(request, order_id):
    order = get_object_or_404(LabOrder, pk=order_id)
    try:
        order = lab.receive_order(order=order, staff=_profile_or_none(request),
                                  barcode=request.POST.get("barcode", ""))
    except LabError as exc:
        messages.error(request, str(exc))
        return _back_to_dashboard("to_receive")
    log_action(request, AuditAction.LAB_SAMPLE_RECEIVED,
               f"Sample received for lab order {order.id} ({order.encounter.patient}).")
    messages.success(request, "Sample received. Paid tests are ready for results.")
    return _back_to_dashboard("to_enter")


# ---------- results and report ----------

@requires(Cap.LAB_ENTER_RESULT)
def result_entry_view(request, order_id):
    order = get_object_or_404(LabOrder.objects.select_related("encounter__patient", "ordered_by__user"),
                              pk=order_id)
    if order.status in ("VERIFIED", "RELEASED", "CANCELLED"):
        messages.error(request, "This order is closed for results.")
        return _back_to_dashboard("released")
    lines = list(order.lines.select_related("test").exclude(status="CANCELLED").order_by("test__section", "test__name"))
    return render(request, "lab/results.html", {"order": order, "lines": lines})


@requires(Cap.LAB_ENTER_RESULT)
@require_POST
def save_results_action(request, order_id):
    order = get_object_or_404(LabOrder, pk=order_id)
    saved, errors = 0, []
    for line in order.lines.filter(status="RECEIVED"):
        raw = request.POST.get(f"value_{line.id}", "")
        if not raw.strip():
            continue
        try:
            lab.enter_result(line=line, staff=_profile_or_none(request), value_raw=raw)
            saved += 1
        except LabError as exc:
            errors.append(str(exc))
    for e in errors:
        messages.error(request, e)
    if saved:
        log_action(request, AuditAction.LAB_RESULT_ENTERED,
                   f"{saved} result(s) on lab order {order.id} ({order.encounter.patient}).")
        messages.success(request, f"{saved} result(s) saved.")
    return redirect("lab:results", order_id=order.id)


@requires(Cap.LAB_ENTER_RESULT)
@require_POST
def submit_report_action(request, order_id):
    order = get_object_or_404(LabOrder, pk=order_id)
    try:
        lab.submit_report(order=order, staff=_profile_or_none(request),
                          text=request.POST.get("report", ""))
    except LabError as exc:
        messages.error(request, str(exc))
        return redirect("lab:results", order_id=order.id)
    log_action(request, AuditAction.LAB_REPORT_SUBMITTED,
               f"Report submitted for lab order {order.id} ({order.encounter.patient}).")
    messages.success(request, "Report submitted for verification.")
    return _back_to_dashboard("to_verify")


# ---------- verification and release ----------

@requires(Cap.LAB_VERIFY)
def verify_view(request, order_id):
    order = get_object_or_404(LabOrder.objects.select_related("encounter__patient", "ordered_by__user"),
                              pk=order_id)
    if order.status != "RESULTED" or not order.report:
        messages.error(request, "This order is not ready for verification.")
        return _back_to_dashboard("to_enter")
    profile = _profile_or_none(request)
    lines = list(order.lines.select_related("test").exclude(status="CANCELLED").order_by("test__section", "test__name"))
    is_entrant = profile is not None and profile.id in _entrant_ids(order)
    return render(request, "lab/verify.html", {
        "order": order, "lines": lines, "is_entrant": is_entrant,
    })


@requires(Cap.LAB_VERIFY)
@require_POST
def verify_release_action(request, order_id):
    order = get_object_or_404(LabOrder, pk=order_id)
    try:
        lab.verify_and_release(order=order, supervisor=_profile_or_none(request))
    except LabError as exc:
        messages.error(request, str(exc))
        return redirect("lab:verify", order_id=order.id)
    log_action(request, AuditAction.LAB_RELEASED,
               f"Released lab order {order.id} for {order.encounter.patient}.")
    messages.success(request, "Results released to the doctor.")
    return _back_to_dashboard("released")


@requires(Cap.LAB_VIEW)
def order_print_view(request, order_id):
    order = get_object_or_404(
        LabOrder.objects.select_related("encounter__patient", "ordered_by__user",
                                        "verified_by__user", "released_by__user"),
        pk=order_id)
    if order.status != "RELEASED":
        messages.error(request, "Only a released order can be printed.")
        return _back_to_dashboard("to_verify")
    lines = list(order.lines.select_related("test").exclude(status="CANCELLED")
                 .prefetch_related("amendments__amended_by__user")
                 .order_by("test__section", "test__name"))
    log_action(request, AuditAction.ENCOUNTER_RECORD_VIEWED,
               f"Printed lab report {order.id} for {order.encounter.patient}.")
    return render(request, "lab/print.html", {
        "order": order, "lines": lines,
        "can_amend": Cap.LAB_AMEND in caps_for(request.user),
    })


# ---------- cancellation and amendment ----------

@requires(Cap.LAB_VIEW)
@require_POST
def cancel_line_action(request, line_id):
    line = get_object_or_404(LabOrderLine.objects.select_related("order"), pk=line_id)
    profile = _profile_or_none(request)
    caps = caps_for(request.user)
    is_ordering_doctor = profile is not None and profile.id == line.order.ordered_by_id
    if not (Cap.LAB_ENTER_RESULT in caps or is_ordering_doctor):
        messages.error(request, "You cannot cancel this test.")
        return redirect("lab:dashboard")
    try:
        lab.cancel_line(line=line, staff=profile, reason=request.POST.get("reason", ""))
    except LabError as exc:
        messages.error(request, str(exc))
    else:
        log_action(request, AuditAction.LAB_LINE_CANCELLED,
                   f"Cancelled {line.test.name} on lab order {line.order_id}: {request.POST.get('reason', '').strip()}")
        messages.success(request, "Test cancelled.")
    if is_ordering_doctor and Cap.LAB_ENTER_RESULT not in caps:
        return redirect("doctors_portal:consultation_room", encounter_id=line.order.encounter_id)
    return _back_to_dashboard("to_receive")


@requires(Cap.LAB_AMEND)
@require_POST
def amend_result_action(request, line_id):
    line = get_object_or_404(LabOrderLine.objects.select_related("order", "test"), pk=line_id)
    try:
        lab.amend_result(line=line, supervisor=_profile_or_none(request),
                         value_raw=request.POST.get("value", ""),
                         reason=request.POST.get("reason", ""))
    except LabError as exc:
        messages.error(request, str(exc))
        return redirect("lab:print", order_id=line.order_id)
    log_action(request, AuditAction.LAB_RESULT_AMENDED,
               f"Amended {line.test.name} on lab order {line.order_id}: {request.POST.get('reason', '').strip()}")
    messages.success(request, "Result corrected. The original value is kept in the amendment history.")
    return redirect("lab:print", order_id=line.order_id)
