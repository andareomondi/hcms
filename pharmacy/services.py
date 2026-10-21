from django.db import transaction
from django.db.models import Sum

from core_patients.allergy_services import conflicts_for

from .models import (
    Dispensation,
    MedicationOrder,
    NursingAdministration,
    StockItem,
    StockMovement,
)


class PharmacyError(Exception):
    """A rule staff must see. Shown as-is."""


def _dispensed_total(order):
    return order.dispensations.aggregate(t=Sum("quantity"))["t"] or 0


def _require_formulary(order):
    """External prescriptions are never supplied by the hospital (QOL-1)."""
    if order.source != "FORMULARY" or order.medication_id is None:
        raise PharmacyError("External prescriptions are not supplied by the hospital.")


def allergy_hits(order):
    if order.medication_id is None:
        return []
    return list(conflicts_for(order.encounter.patient, order.medication))


def check_allergies(order):
    """
    Blocks a medicine the patient is allergic to, unless the prescribing doctor
    documented an override on the order.
    """
    hits = allergy_hits(order)
    if hits and not order.allergy_override_reason:
        names = ", ".join(h.allergen.display_name for h in hits)
        raise PharmacyError(
            f"Allergy alert: the patient is recorded as allergic to {names}. "
            "The prescribing doctor must document an override before this medicine can be given."
        )


def _lock_order(order_id):
    return (MedicationOrder.objects.select_for_update()
            .select_related("medication", "encounter__patient")
            .get(pk=order_id))


def _move_stock(medication, delta, reason, staff, reference_id):
    """Change stock and write the movement row. Must run inside a transaction."""
    stock = StockItem.objects.select_for_update().filter(medication=medication).first()
    if stock is None:
        raise PharmacyError(f"{medication.brand_name} has no stock record. Add stock first.")
    if stock.quantity + delta < 0:
        raise PharmacyError(f"Not enough {medication.brand_name} in stock ({stock.quantity} left).")
    stock.quantity += delta
    stock.save(update_fields=["quantity"])
    StockMovement.objects.create(medication=medication, delta=delta, reason=reason,
                                 reference_id=reference_id, actor=staff)
    return stock


@transaction.atomic
def receive_stock(*, medication, quantity, staff):
    if quantity < 1:
        raise PharmacyError("The quantity received must be at least 1.")
    stock, _ = (StockItem.objects.select_for_update()
                .get_or_create(medication=medication, defaults={"quantity": 0, "reorder_level": 50}))
    stock.quantity += quantity
    stock.save(update_fields=["quantity"])
    StockMovement.objects.create(medication=medication, delta=quantity, reason="RECEIPT", actor=staff)
    return stock


@transaction.atomic
def adjust_stock(*, medication, delta, staff):
    if delta == 0:
        raise PharmacyError("An adjustment cannot be zero.")
    return _move_stock(medication, delta, "ADJUST", staff, None)


@transaction.atomic
def dispense(*, order_id, quantity, staff, serials=None):
    order = _lock_order(order_id)
    _require_formulary(order)
    if order.fulfilment != "PHARMACY_DISPENSE":
        raise PharmacyError("This medicine is given by a nurse, not dispensed.")
    if order.status not in ("ORDERED", "PARTIALLY_DONE"):
        raise PharmacyError("This order is no longer open.")
    check_allergies(order)

    done = _dispensed_total(order)
    remaining = order.doses_total - done
    if not (1 <= quantity <= remaining):
        raise PharmacyError(f"Enter a quantity between 1 and {remaining}.")

    # take_doses is the pack 10 serial function. It is not in the live file supplied
    # on 2026-10-21 and must be restored (QOL-2 preconditions).
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
    check_allergies(order)

    given = order.administrations.count()
    if given >= order.doses_total:
        raise PharmacyError("All doses for this order are already recorded.")

    # Pack 10 path. Serial handling is restored with take_doses (QOL-2 preconditions).
    take_doses(medication=order.medication, doses=1, staff=nurse,
               reason="ADMINISTER", reference_id=order.id, serials=serials)
    NursingAdministration.objects.create(order=order, administered_by=nurse,
                                         dose_given_text=dose_given_text.strip(),
                                         site=site.strip(), notes=notes.strip())
    given += 1
    order.status = "COMPLETED" if given >= order.doses_total else "PARTIALLY_DONE"
    order.save(update_fields=["status"])
    return order
