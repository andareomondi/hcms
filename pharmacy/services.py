from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from billing import refunds as billing_refunds
from billing import services as billing
from billing.prices import CONSULTATION_FEE  # noqa: F401  (kept for parity with other services)
from billing.services import medication_charge_or_none
from core_patients.allergy_services import conflicts_for

from .models import (
    Dispensation,
    MedicationOrder,
    NursingAdministration,
    StockBatch,
    StockItem,
    StockMovement,
    StockUnit,
)

OPEN_ORDER_STATUSES = ("ORDERED", "PARTIALLY_DONE")


class PharmacyError(Exception):
    """A rule staff must see. Shown as-is."""


class PrepayError(PharmacyError):
    """A medicine is not paid for. Shown as-is."""


class StockShortError(PharmacyError):
    """Not enough usable stock for the requested doses. The order can be marked short."""


# ---------- helpers ----------

def _dispensed_total(order):
    return order.dispensations.aggregate(t=Sum("quantity"))["t"] or 0


def _require_formulary(order):
    """External prescriptions are never supplied by the hospital (QOL-1)."""
    if order.source != "FORMULARY" or order.medication_id is None:
        raise PharmacyError("External prescriptions are not supplied by the hospital.")


def _lock_order(order_id):
    return (MedicationOrder.objects
            .select_for_update(of=("self",))
            .select_related("medication", "encounter__patient")
            .get(pk=order_id))


def usable_doses(medication, today=None):
    """Doses in unexpired, in-stock units. Use this for every stock figure shown to staff."""
    today = today or timezone.localdate()
    total = (StockUnit.objects
             .filter(medication=medication, status="IN_STOCK", doses_remaining__gt=0,
                     batch__expiry_date__gte=today)
             .aggregate(t=Sum("doses_remaining"))["t"])
    return total or 0


def remaining_doses(order):
    """Doses an open order still needs: prescribed, minus dispensed and given."""
    dispensed = sum(d.quantity for d in order.dispensations.all())
    given = len(order.administrations.all())
    return max(0, order.doses_total - dispensed - given)


def committed_doses(medication, exclude_order_id=None):
    """Doses already promised to open formulary orders for this medicine."""
    qs = (MedicationOrder.objects
          .filter(medication=medication, source="FORMULARY", status__in=OPEN_ORDER_STATUSES)
          .prefetch_related("dispensations", "administrations"))
    if exclude_order_id:
        qs = qs.exclude(pk=exclude_order_id)
    return sum(remaining_doses(o) for o in qs)


def available_to_prescribe(medication, today=None):
    """Usable stock minus what open orders already need. Never negative."""
    return max(0, usable_doses(medication, today) - committed_doses(medication))


# ---------- expiry (automatic) ----------

@transaction.atomic
def expire_units(*, staff, today=None):
    """
    Writes off every in-stock unit whose batch expiry date is before today.
    Idempotent: a unit is only touched while its status is IN_STOCK.
    """
    today = today or timezone.localdate()
    expired = list(StockUnit.objects.select_for_update().select_related("medication", "batch")
                   .filter(status="IN_STOCK", batch__expiry_date__lt=today))
    for unit in expired:
        if unit.doses_remaining:
            consume_unit(unit=unit, doses=unit.doses_remaining, staff=staff, reason="EXPIRE")
        unit.status = "EXPIRED"
        unit.save(update_fields=["status"])
    return expired


def expire_due_units(*, staff, today=None):
    return expire_units(staff=staff, today=today)


# ---------- payment ----------

def is_prepaid(order):
    if order.encounter.prepay_bypassed:
        return True
    if order.credit_approved_by_id:
        return True
    charge = medication_charge_or_none(order)
    return charge is not None and billing.charge_fully_paid(charge)


def check_prepaid(order):
    _require_formulary(order)
    if is_prepaid(order):
        return
    charge = medication_charge_or_none(order)
    if charge is None:
        raise PrepayError("No charge exists for this medicine. Ask the doctor to re-issue the prescription.")
    raise PrepayError(
        f"Awaiting payment: KES {billing.outstanding_amount(charge):,.2f} still owed on "
        f"{order.medication.brand_name}. The patient must pay at billing, or billing must approve a credit exception."
    )


# ---------- allergies ----------

def allergy_hits(order):
    if order.medication_id is None:
        return []
    return list(conflicts_for(order.encounter.patient, order.medication))


def check_allergies(order):
    hits = allergy_hits(order)
    if hits and not order.allergy_override_reason:
        names = ", ".join(h.allergen.display_name for h in hits)
        raise PharmacyError(
            f"Allergy alert: the patient is recorded as allergic to {names}. "
            "The prescribing doctor must document an override before this medicine can be given."
        )


# ---------- unit checks ----------

def unit_problem(unit, today=None):
    today = today or timezone.localdate()
    if unit.status != "IN_STOCK" or unit.doses_remaining <= 0:
        return "this pack or vial is no longer in stock"
    if unit.batch.expiry_date < today:
        return f"expired on {unit.batch.expiry_date:%d %b %Y}"
    hours = unit.medication.in_use_hours
    if unit.opened_at and hours and timezone.now() > unit.opened_at + timedelta(hours=hours):
        return f"opened more than {hours} hour(s) ago and must be discarded"
    return None


# ---------- prescribing ----------

@transaction.atomic
def create_formulary_order(*, encounter, medication, doctor, route, frequency, dose_text,
                           days, doses_total, override, staff):
    """
    Creates a formulary order only if the hospital can supply every dose. The medicine's stock
    row is locked for the duration, so two prescribers cannot both claim the same last doses.
    Raises StockShortError otherwise; the caller then offers an external prescription.
    """
    StockItem.objects.select_for_update().filter(medication=medication).first()
    available = available_to_prescribe(medication)
    if doses_total > available:
        raise StockShortError(
            f"Only {available} dose(s) of {medication.brand_name} can be prescribed now; "
            f"{doses_total} are needed."
        )
    fulfilment = "PHARMACY_DISPENSE" if route == "ORAL" else "NURSE_ADMINISTER"
    order = MedicationOrder.objects.create(
        encounter=encounter, ordered_by=doctor, medication=medication, source="FORMULARY",
        route=route, fulfilment=fulfilment, dose_text=dose_text or medication.strength,
        frequency_text=frequency, duration_days=days, doses_total=doses_total,
        status="ORDERED", allergy_override_reason=override,
    )
    billing.add_charge(
        encounter=encounter, source_type="MEDICATION_ORDER",
        description=f"{medication.brand_name} {medication.strength} x{doses_total}",
        unit_price=medication.unit_price, quantity=doses_total, staff=staff,
        source_id=order.id, key=f"med:{order.id}",
    )
    return order


# ---------- receiving ----------

@transaction.atomic
def receive_batch(*, medication, batch_number, expiry_date, serials, doses_per_unit, staff):
    batch_number = (batch_number or "").strip().upper()
    if not batch_number:
        raise PharmacyError("Enter the batch number from the box or vial label.")
    if expiry_date is None or expiry_date <= timezone.localdate():
        raise PharmacyError("The expiry date must be in the future.")
    if not serials:
        raise PharmacyError("Scan at least one serial.")
    per_unit = doses_per_unit or medication.doses_per_unit
    if per_unit < 1:
        raise PharmacyError("Doses per unit must be at least 1.")
    if StockBatch.objects.filter(medication=medication, batch_number=batch_number).exists():
        raise PharmacyError(f"Batch {batch_number} is already received for {medication.brand_name}.")

    taken = list(StockUnit.objects.filter(serial__in=serials).values_list("serial", flat=True))
    if taken:
        raise PharmacyError("These serials are already in the system: " + ", ".join(taken[:10]))

    batch = StockBatch.objects.create(medication=medication, batch_number=batch_number,
                                      expiry_date=expiry_date, received_by=staff)
    units = StockUnit.objects.bulk_create([
        StockUnit(serial=s, medication=medication, batch=batch,
                  doses_total=per_unit, doses_remaining=per_unit)
        for s in serials
    ])
    stock, _ = (StockItem.objects.select_for_update()
                .get_or_create(medication=medication, defaults={"quantity": 0, "reorder_level": 10}))
    stock.quantity += per_unit * len(units)
    stock.save(update_fields=["quantity"])
    StockMovement.objects.bulk_create([
        StockMovement(medication=medication, unit=u, delta=per_unit, reason="RECEIPT", actor=staff)
        for u in units
    ])
    return batch, units


# ---------- consuming ----------

def consume_unit(*, unit, doses, staff, reason, reference_id=None):
    if doses < 1 or doses > unit.doses_remaining:
        raise PharmacyError(f"Cannot take {doses} dose(s) from {unit.serial}.")
    if unit.opened_at is None and unit.doses_total > 1:
        unit.opened_at = timezone.now()
    unit.doses_remaining -= doses
    if unit.doses_remaining == 0:
        unit.status = "EMPTY"
    unit.save(update_fields=["doses_remaining", "status", "opened_at"])

    stock = StockItem.objects.select_for_update().get(medication=unit.medication_id)
    if stock.quantity < doses:
        raise PharmacyError("Stock records are out of step. Ask an administrator to run reconcile_stock.")
    stock.quantity -= doses
    stock.save(update_fields=["quantity"])
    StockMovement.objects.create(medication_id=unit.medication_id, unit=unit, delta=-doses,
                                 reason=reason, reference_id=reference_id, actor=staff)


def take_doses(*, medication, doses, staff, reason, reference_id, serials=None):
    """
    Takes doses from scanned serials, or from the earliest expiry. Raises StockShortError when
    the physical stock cannot cover the doses. Expired stock is written off first.
    """
    if doses < 1:
        raise PharmacyError("Quantity must be at least 1.")
    expire_due_units(staff=staff)
    today = timezone.localdate()

    if serials:
        units = []
        for serial in serials:
            unit = (StockUnit.objects.select_for_update().select_related("batch", "medication")
                    .filter(serial=serial).first())
            if unit is None:
                raise PharmacyError(f"{serial} is not in the system. Receive it first.")
            if unit.medication_id != medication.id:
                raise PharmacyError(f"{serial} is not {medication.brand_name}.")
            problem = unit_problem(unit, today)
            if problem:
                raise PharmacyError(f"{serial}: {problem}.")
            units.append(unit)
        available = sum(u.doses_remaining for u in units)
        if available < doses:
            raise StockShortError(f"The scanned packs hold {available} dose(s); {doses} are needed.")
    else:
        if medication.requires_serial_scan:
            raise PharmacyError(f"Scan the vial or pack for {medication.brand_name}. "
                                "This medicine cannot be picked automatically.")
        units = list(StockUnit.objects.select_for_update()
                     .select_related("batch", "medication")
                     .filter(medication=medication, status="IN_STOCK", doses_remaining__gt=0,
                             batch__expiry_date__gte=today)
                     .order_by("batch__expiry_date", "received_at"))
        available = sum(u.doses_remaining for u in units)
        if available < doses:
            raise StockShortError(f"Only {available} dose(s) of {medication.brand_name} are in stock.")

    remaining = doses
    for unit in units:
        if remaining == 0:
            break
        take = min(unit.doses_remaining, remaining)
        consume_unit(unit=unit, doses=take, staff=staff, reason=reason, reference_id=reference_id)
        remaining -= take
    return doses


# ---------- dispensing, administration ----------

@transaction.atomic
def dispense(*, order_id, quantity, staff, serials=None):
    order = _lock_order(order_id)
    _require_formulary(order)
    if order.fulfilment != "PHARMACY_DISPENSE":
        raise PharmacyError("This medicine is given by a nurse, not dispensed.")
    if order.status not in OPEN_ORDER_STATUSES:
        raise PharmacyError("This order is no longer open.")
    check_prepaid(order)
    check_allergies(order)

    remaining = remaining_doses(order)
    if not (1 <= quantity <= remaining):
        raise PharmacyError(f"Enter a quantity between 1 and {remaining}.")

    take_doses(medication=order.medication, doses=quantity, staff=staff,
               reason="DISPENSE", reference_id=order.id, serials=serials)
    Dispensation.objects.create(order=order, quantity=quantity, dispensed_by=staff)
    order.status = "COMPLETED" if quantity == remaining else "PARTIALLY_DONE"
    order.save(update_fields=["status"])
    return order


@transaction.atomic
def administer(*, order_id, nurse, dose_given_text, site="", notes="", serials=None):
    order = _lock_order(order_id)
    _require_formulary(order)
    if order.fulfilment != "NURSE_ADMINISTER":
        raise PharmacyError("This medicine is dispensed by pharmacy, not given by a nurse.")
    if order.status not in OPEN_ORDER_STATUSES:
        raise PharmacyError("This order is no longer open.")
    if not dose_given_text.strip():
        raise PharmacyError("Record the dose that was given.")
    check_prepaid(order)
    check_allergies(order)
    if remaining_doses(order) < 1:
        raise PharmacyError("All doses for this order are already recorded.")

    take_doses(medication=order.medication, doses=1, staff=nurse,
               reason="ADMINISTER", reference_id=order.id, serials=serials)
    NursingAdministration.objects.create(order=order, administered_by=nurse,
                                         dose_given_text=dose_given_text.strip(),
                                         site=site.strip(), notes=notes.strip())
    order.status = "COMPLETED" if remaining_doses(order) == 0 else "PARTIALLY_DONE"
    order.save(update_fields=["status"])
    return order


# ---------- stock shortage after prescribing, and cancellation ----------

@transaction.atomic
def mark_stock_short(*, order_id, staff, reason):
    """
    The hospital cannot supply an open order. The order is closed as STOCK_SHORT. Its charge
    is voided; any money already paid becomes a refund request for billing. The prescriber
    must issue an external prescription if the patient still needs the medicine.
    """
    order = _lock_order(order_id)
    if order.status not in OPEN_ORDER_STATUSES:
        raise PharmacyError("This order is no longer open.")
    order.status = "STOCK_SHORT"
    order.cancelled_reason = f"Stock short: {reason}"[:2000]
    order.save(update_fields=["status", "cancelled_reason"])
    billing_refunds.close_order_charge(
        charge=medication_charge_or_none(order), staff=staff,
        reason=f"Medicine could not be supplied: {order.medication.brand_name}. {reason}",
    )
    return order


@transaction.atomic
def cancel_medication_order(*, order_id, staff, reason):
    """
    Cancels an open order before anything was dispensed or given. Committed doses are released
    automatically because they are counted from open orders.
    """
    reason = (reason or "").strip()
    if not reason:
        raise PharmacyError("Give a reason for cancelling the prescription.")
    order = _lock_order(order_id)
    if order.status not in OPEN_ORDER_STATUSES:
        raise PharmacyError("This order is no longer open.")
    if remaining_doses(order) != order.doses_total:
        raise PharmacyError("Part of this order has already been dispensed or given. It cannot be cancelled.")
    order.status = "CANCELLED"
    order.cancelled_reason = reason[:2000]
    order.save(update_fields=["status", "cancelled_reason"])
    billing_refunds.close_order_charge(
        charge=medication_charge_or_none(order), staff=staff,
        reason=f"Prescription cancelled: {order.medication.brand_name}. {reason}",
    )
    return order


@transaction.atomic
def write_off(*, serial, reason, staff):
    if not reason.strip():
        raise PharmacyError("Give a reason for writing off this pack or vial.")
    unit = (StockUnit.objects.select_for_update().select_related("medication")
            .filter(serial=serial).first())
    if unit is None:
        raise PharmacyError("Serial not found.")
    if unit.status in ("EMPTY", "WRITTEN_OFF", "EXPIRED"):
        raise PharmacyError(f"{serial} is already closed ({unit.get_status_display().lower()}).")
    if unit.doses_remaining:
        consume_unit(unit=unit, doses=unit.doses_remaining, staff=staff, reason="WRITE_OFF")
    unit.status = "WRITTEN_OFF"
    unit.save(update_fields=["status"])
    return unit
