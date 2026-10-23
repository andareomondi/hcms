from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from billing import services as billing
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


class PharmacyError(Exception):
    """A rule staff must see. Shown as-is."""


class PrepayError(PharmacyError):
    """A medicine is not paid for. Shown as-is."""


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


# ---------- payment ----------

def is_prepaid(order):
    """True when the medicine may be released now."""
    if order.encounter.prepay_bypassed:
        return True
    if order.credit_approved_by_id:
        return True
    charge = medication_charge_or_none(order)
    return charge is not None and billing.charge_fully_paid(charge)


def check_prepaid(order):
    """
    Pre-pay gate. A formulary medicine is released only when its charge is fully paid,
    the visit is an emergency bypass, or billing approved a credit exception.
    """
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
    """Returns why a unit can't be used right now, or None if it can."""
    today = today or timezone.localdate()
    if unit.status != "IN_STOCK" or unit.doses_remaining <= 0:
        return "this pack or vial is no longer in stock"
    if unit.batch.expiry_date < today:
        return f"expired on {unit.batch.expiry_date:%d %b %Y}"
    hours = unit.medication.in_use_hours
    if unit.opened_at and hours and timezone.now() > unit.opened_at + timedelta(hours=hours):
        return f"opened more than {hours} hour(s) ago and must be discarded"
    return None


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

    total = per_unit * len(units)
    stock, _ = (StockItem.objects.select_for_update()
                .get_or_create(medication=medication, defaults={"quantity": 0, "reorder_level": 50}))
    stock.quantity += total
    stock.save(update_fields=["quantity"])
    StockMovement.objects.bulk_create([
        StockMovement(medication=medication, unit=u, delta=per_unit, reason="RECEIPT", actor=staff)
        for u in units
    ])
    return batch, units


# ---------- consuming ----------

def consume_unit(*, unit, doses, staff, reason, reference_id=None):
    """Takes doses from one locked unit and keeps the medicine total in step."""
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
    Takes doses from scanned serials when given, otherwise picks from the earliest expiry.
    Medicines marked requires_serial_scan (vaccines) must always be scanned.
    """
    if doses < 1:
        raise PharmacyError("Quantity must be at least 1.")
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
            raise PharmacyError(f"The scanned packs hold {available} dose(s); {doses} are needed.")
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
            raise PharmacyError(f"Only {available} dose(s) of {medication.brand_name} are in stock.")

    remaining = doses
    for unit in units:
        if remaining == 0:
            break
        take = min(unit.doses_remaining, remaining)
        consume_unit(unit=unit, doses=take, staff=staff, reason=reason, reference_id=reference_id)
        remaining -= take
    return doses


@transaction.atomic
def lock_unit_for_vaccine(*, serial, vaccine_code):
    """Locks and checks the vial a vaccine is being taken from."""
    unit = (StockUnit.objects.select_for_update().select_related("batch", "medication")
            .filter(serial=serial).first())
    if unit is None:
        raise PharmacyError(f"{serial} is not in the system. Receive the vial first.")
    if unit.medication.vaccine_code != vaccine_code:
        raise PharmacyError(f"{serial} is {unit.medication.brand_name}, which is not this vaccine.")
    problem = unit_problem(unit)
    if problem:
        raise PharmacyError(f"{serial}: {problem}.")
    return unit


# ---------- dispensing, administration, write-off ----------

@transaction.atomic
def dispense(*, order_id, quantity, staff, serials=None):
    order = _lock_order(order_id)
    _require_formulary(order)
    if order.fulfilment != "PHARMACY_DISPENSE":
        raise PharmacyError("This medicine is given by a nurse, not dispensed.")
    if order.status not in ("ORDERED", "PARTIALLY_DONE"):
        raise PharmacyError("This order is no longer open.")
    check_prepaid(order)
    check_allergies(order)

    done = _dispensed_total(order)
    remaining = order.doses_total - done
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
    if order.status not in ("ORDERED", "PARTIALLY_DONE"):
        raise PharmacyError("This order is no longer open.")
    if not dose_given_text.strip():
        raise PharmacyError("Record the dose that was given.")
    check_prepaid(order)
    check_allergies(order)
    if order.administrations.count() >= order.doses_total:
        raise PharmacyError("All doses for this order are already recorded.")

    take_doses(medication=order.medication, doses=1, staff=nurse,
               reason="ADMINISTER", reference_id=order.id, serials=serials)
    NursingAdministration.objects.create(order=order, administered_by=nurse,
                                         dose_given_text=dose_given_text.strip(),
                                         site=site.strip(), notes=notes.strip())
    given = order.administrations.count()
    order.status = "COMPLETED" if given >= order.doses_total else "PARTIALLY_DONE"
    order.save(update_fields=["status"])
    return order


@transaction.atomic
def write_off(*, serial, reason, staff):
    if not reason.strip():
        raise PharmacyError("Give a reason for writing off this pack or vial.")
    unit = (StockUnit.objects.select_for_update().select_related("medication")
            .filter(serial=serial).first())
    if unit is None:
        raise PharmacyError("Serial not found.")
    if unit.status in ("EMPTY", "WRITTEN_OFF"):
        raise PharmacyError(f"{serial} is already closed ({unit.get_status_display().lower()}).")
    if unit.doses_remaining:
        consume_unit(unit=unit, doses=unit.doses_remaining, staff=staff, reason="WRITE_OFF")
    unit.status = "WRITTEN_OFF"
    unit.save(update_fields=["status"])
    return unit


@transaction.atomic
def expire_units(*, staff, today=None):
    """Writes off every unit past its expiry date."""
    today = today or timezone.localdate()
    expired = list(StockUnit.objects.select_for_update().select_related("medication", "batch")
                   .filter(status="IN_STOCK", batch__expiry_date__lt=today))
    for unit in expired:
        if unit.doses_remaining:
            consume_unit(unit=unit, doses=unit.doses_remaining, staff=staff, reason="EXPIRE")
        unit.status = "EXPIRED"
        unit.save(update_fields=["status"])
    return expired
