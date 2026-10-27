from decimal import Decimal

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from billing import services as billing
from notifications.services import notify, notify_on_duty
from triage_queue.services import notify_results_ready

from .models import LabOrder, LabOrderLine, LabResultAmendment, LabTest

REPORT_MIN_CHARS = 100


class LabError(Exception):
    """A rule staff must see. Shown as-is."""


# ---------- payment gate ----------

def line_prepaid(line):
    """A line is processed only when paid, emergency-bypassed, or credit-approved."""
    encounter = line.order.encounter
    if encounter.prepay_bypassed or line.credit_approved_by_id:
        return True
    charge = lab_charge_or_none(line)
    return charge is not None and billing.charge_fully_paid(charge)


def lab_charge_or_none(line):
    return billing.lab_charge_or_none(line)


def _active_lines(order):
    return order.lines.exclude(status="CANCELLED")


# ---------- ordering ----------

@transaction.atomic
def order_tests(*, encounter, doctor, test_ids, indication, staff):
    """
    Creates one order with one line per test, and one charge per line.
    Pauses the consultation to AWAITING_RESULTS, as scans do.
    """
    from triage_queue.services import pause_for_results

    indication = (indication or "").strip()
    if not indication:
        raise LabError("Give the clinical indication for the tests.")
    ids = list(dict.fromkeys(test_ids))  # de-duplicate, keep order
    if not ids:
        raise LabError("Choose at least one test.")
    tests = list(LabTest.objects.filter(pk__in=ids, is_active=True))
    if len(tests) != len(ids):
        raise LabError("One of the chosen tests is not available. Refresh the list and try again.")

    order = LabOrder.objects.create(encounter=encounter, ordered_by=doctor, indication=indication)
    for test in tests:
        line = LabOrderLine.objects.create(order=order, test=test, price_snapshot=test.price)
        billing.add_charge(
            encounter=encounter, source_type="LAB_TEST",
            description=f"Lab: {test.name}", unit_price=test.price, staff=staff,
            source_id=line.id, key=f"lab:{line.id}",
        )

    paused = pause_for_results(encounter, doctor)
    notify_on_duty(role="LAB_TECHNICIAN", kind="SYSTEM",
                   message=f"New lab order: {len(tests)} test(s) for {encounter.patient}.",
                   link=reverse("lab:dashboard"))
    return order, bool(paused)


# ---------- reception of the sample ----------

@transaction.atomic
def receive_order(*, order, staff, barcode=""):
    """
    Receives the sample. Only paid (or credit-approved, or bypassed) lines are received.
    Unpaid lines stay ORDERED and are shown on the lab dashboard as awaiting payment.
    """
    order = LabOrder.objects.select_for_update().get(pk=order.pk)
    if order.status == "CANCELLED":
        raise LabError("This order has been cancelled.")
    if order.status != "ORDERED":
        raise LabError("This sample has already been received.")

    payable = [l for l in _active_lines(order) if line_prepaid(l)]
    if not payable:
        raise LabError("No test on this order is paid for yet. The patient must pay at billing first.")

    barcode = (barcode or "").strip().upper() or None
    if barcode and LabOrder.objects.filter(sample_barcode=barcode).exclude(pk=order.pk).exists():
        raise LabError("This sample barcode is already used on another order.")

    for line in payable:
        line.status = "RECEIVED"
        line.save(update_fields=["status"])
    order.status = "RECEIVED"
    order.received_by = staff
    order.received_at = timezone.now()
    order.sample_barcode = barcode or order.sample_barcode
    order.save(update_fields=["status", "received_by", "received_at", "sample_barcode"])
    return order


# ---------- results ----------

def compute_flag(test, value):
    if test.result_type != "NUMERIC" or value is None:
        return ""
    if test.critical_low is not None and value <= test.critical_low:
        return "CRITICAL"
    if test.critical_high is not None and value >= test.critical_high:
        return "CRITICAL"
    if test.reference_low is not None and value < test.reference_low:
        return "LOW"
    if test.reference_high is not None and value > test.reference_high:
        return "HIGH"
    return "NORMAL"


@transaction.atomic
def enter_result(*, line, staff, value_raw):
    """
    Records one result. Only for a received, processable line. A CRITICAL value notifies
    the ordering doctor at once, before verification.
    """
    line = LabOrderLine.objects.select_for_update().select_related(
        "test", "order__encounter__patient", "order__ordered_by").get(pk=line.pk)
    if line.status != "RECEIVED":
        raise LabError("This test is not ready for a result. Receive the sample first, and check payment.")
    if not line_prepaid(line):
        raise LabError("Awaiting payment. This test cannot be processed until it is paid for.")
    if line.order.status in ("VERIFIED", "RELEASED", "CANCELLED"):
        raise LabError("This order is no longer open for results.")

    value_raw = (value_raw or "").strip()
    test = line.test
    flag = ""
    if test.result_type == "NUMERIC":
        try:
            number = Decimal(value_raw)
        except Exception:
            raise LabError(f"{test.name}: enter a number.")
        line.value_number = number
        line.value_text = ""
        flag = compute_flag(test, number)
    elif test.result_type == "POSITIVE_NEGATIVE":
        if value_raw.upper() not in ("POSITIVE", "NEGATIVE"):
            raise LabError(f"{test.name}: choose Positive or Negative.")
        line.value_number = None
        line.value_text = value_raw.upper()
        flag = value_raw.upper()
    else:
        if not value_raw:
            raise LabError(f"{test.name}: enter a result.")
        line.value_number = None
        line.value_text = value_raw[:255]
        flag = "NORMAL" if not test.reference_text else (
            "NORMAL" if value_raw.lower() == test.reference_text.lower() else "ABNORMAL")

    line.flag = flag
    line.status = "RESULTED"
    line.entered_by = staff
    line.entered_at = timezone.now()
    line.save(update_fields=["value_number", "value_text", "flag", "status", "entered_by", "entered_at"])

    if line.order.status == "RECEIVED":
        line.order.status = "RESULTED"
        line.order.save(update_fields=["status"])

    if flag == "CRITICAL":
        notify([line.order.ordered_by], kind="SYSTEM",
               message=f"CRITICAL result: {test.name} for {line.order.encounter.patient}. Review now.",
               link=reverse("doctors_portal:consultation_room", args=[line.order.encounter_id]))
    return line


@transaction.atomic
def submit_report(*, order, staff, text):
    """Written report, at least REPORT_MIN_CHARS characters, required before verification."""
    order = LabOrder.objects.select_for_update().get(pk=order.pk)
    text = (text or "").strip()
    if len(text) < REPORT_MIN_CHARS:
        raise LabError(f"The report must be at least {REPORT_MIN_CHARS} characters. "
                       f"It is {len(text)} now.")
    if order.status not in ("RECEIVED", "RESULTED"):
        raise LabError("This order is not in a state that takes a report.")
    open_lines = _active_lines(order).exclude(status="RESULTED").exclude(status="RELEASED")
    if open_lines.exists():
        raise LabError("Every test must have a result, or be cancelled, before the report is submitted.")

    order.report = text
    order.report_by = staff
    order.report_at = timezone.now()
    order.save(update_fields=["report", "report_by", "report_at"])
    return order


@transaction.atomic
def verify_and_release(*, order, supervisor):
    """
    Second-person verification and release. The verifier must not be whoever entered
    the results or the report. Single-person release is not implemented yet.
    """
    order = LabOrder.objects.select_for_update().select_related("encounter", "ordered_by").get(pk=order.pk)
    if not order.second_person_verification:
        raise LabError("Single-person release is not yet implemented. Keep second-person verification on.")
    if order.status != "RESULTED" or not order.report:
        raise LabError("This order needs every result and the written report before it can be released.")

    entrants = set(_active_lines(order).values_list("entered_by_id", flat=True)) | {order.report_by_id}
    if supervisor.id in entrants:
        raise LabError("You entered results or the report on this order. A different person must verify it.")

    now = timezone.now()
    order.status = "RELEASED"
    order.verified_by = supervisor
    order.verified_at = now
    order.released_by = supervisor
    order.released_at = now
    order.save(update_fields=["status", "verified_by", "verified_at", "released_by", "released_at"])
    _active_lines(order).update(status="RELEASED")

    notify([order.ordered_by], kind="SYSTEM",
           message=f"Lab results released: {order.encounter.patient}. Resume from your dashboard.",
           link=reverse("doctors_portal:dashboard"))
    notify_results_ready(order.encounter)
    return order


# ---------- cancellation ----------

@transaction.atomic
def cancel_line(*, line, staff, reason):
    """
    Cancels one test that has not been received and has no payment against it.
    Its charge is voided if still provisional. A paid test needs a refund through billing.
    """
    reason = (reason or "").strip()
    if not reason:
        raise LabError("Give a reason for cancelling the test.")
    line = LabOrderLine.objects.select_for_update().select_related("order").get(pk=line.pk)
    if line.status != "ORDERED":
        raise LabError("Only a test that has not been received can be cancelled.")
    charge = lab_charge_or_none(line)
    if charge is not None:
        if billing.paid_amount(charge) > 0:
            raise LabError("This test has been paid for. Refund it through billing before cancelling.")
        billing.void_or_reverse(charge, staff)
    line.status = "CANCELLED"
    line.save(update_fields=["status"])

    order = line.order
    if not _active_lines(order).exists():
        order.status = "CANCELLED"
        order.cancelled_reason = reason
        order.save(update_fields=["status", "cancelled_reason"])
    return line


# ---------- amendment ----------

@transaction.atomic
def amend_result(*, line, supervisor, value_raw, reason):
    """
    Corrects a released result. The original value is kept in LabResultAmendment.
    The person amending must not be the person who entered the original result.
    """
    reason = (reason or "").strip()
    if not reason:
        raise LabError("Give a reason for the correction.")
    line = LabOrderLine.objects.select_for_update().select_related("test", "order__ordered_by", "order__encounter__patient").get(pk=line.pk)
    if line.status != "RELEASED":
        raise LabError("Only a released result can be amended.")
    if line.entered_by_id == supervisor.id:
        raise LabError("The person who entered this result cannot amend it.")

    old_number, old_text, old_flag = line.value_number, line.value_text, line.flag
    value_raw = (value_raw or "").strip()
    test = line.test
    if test.result_type == "NUMERIC":
        try:
            new_number = Decimal(value_raw)
        except Exception:
            raise LabError("Enter a number.")
        new_text, new_flag = "", compute_flag(test, new_number)
    else:
        new_number, new_text = None, value_raw[:255]
        new_flag = value_raw.upper() if test.result_type == "POSITIVE_NEGATIVE" else ""

    LabResultAmendment.objects.create(
        line=line, previous_number=old_number, previous_text=old_text, previous_flag=old_flag,
        new_number=new_number, new_text=new_text, new_flag=new_flag,
        reason=reason, amended_by=supervisor,
    )
    line.value_number, line.value_text, line.flag = new_number, new_text, new_flag
    line.save(update_fields=["value_number", "value_text", "flag"])
    notify([line.order.ordered_by], kind="SYSTEM",
           message=f"Result corrected: {test.name} for {line.order.encounter.patient}.",
           link=reverse("doctors_portal:consultation_room", args=[line.order.encounter_id]))
    return line


# ---------- queries for the dashboard ----------

def outstanding_lab_orders(encounter):
    """Orders that still block the visit: not released and not cancelled."""
    return LabOrder.objects.filter(encounter=encounter).exclude(status__in=["RELEASED", "CANCELLED"])
