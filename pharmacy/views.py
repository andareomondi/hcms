from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core_patients.allergy_services import conflicts_for
from core_patients.models import AllergenClass
from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from hospital_system.permissions import Cap, caps_for, requires

from . import serials as serial_rules
from . import services
from .models import Medication, MedicationOrder, StockItem, StockUnit
from .services import PharmacyError


def _profile(request):
    if request.staff_profile is None:
        raise PharmacyError("Your account has no staff profile.")
    return request.staff_profile


def _int(raw, label):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise PharmacyError(f"{label} must be a whole number.")


def _annotate(orders):
    """Attach allergy hits, doses given, and current stock to each order for the worklists."""
    rows = list(orders)
    for o in rows:
        if o.medication is None:
            o.allergy_hits = []
            o.stock_quantity = None
        else:
            o.allergy_hits = list(conflicts_for(o.encounter.patient, o.medication))
            o.stock_quantity = (StockItem.objects.filter(medication=o.medication)
                                .values_list("quantity", flat=True).first())
        o.dispensed_total = sum(d.quantity for d in o.dispensations.all())
        o.given_total = o.administrations.count()
    return rows


@requires(Cap.VIEW_PHARMACY)
def pharmacy_dashboard_view(request):
    query = request.GET.get("q", "").strip()
    meds = Medication.objects.select_related("stock").prefetch_related("allergen_classes").order_by("brand_name")
    if query:
        meds = meds.filter(Q(brand_name__icontains=query) | Q(generic_name__icontains=query))
    
    page_obj = Paginator(meds, 30).get_page(request.GET.get("page"))

    stocks = list(StockItem.objects.select_related("medication"))
    low = sorted([s for s in stocks if s.quantity <= s.reorder_level], key=lambda s: s.quantity)
    
    context = {
        "page_obj": page_obj,
        "medications": page_obj.object_list,
        "query": query,
        "low_stock": low[:8],
        "low_count": len(low),
        "out_count": sum(1 for s in stocks if s.quantity <= 0),
        "dispense_pending": MedicationOrder.objects.filter(
            source="FORMULARY", fulfilment="PHARMACY_DISPENSE",
            status__in=["ORDERED", "PARTIALLY_DONE"]).count(),
        "nurse_pending": MedicationOrder.objects.filter(
            source="FORMULARY", fulfilment="NURSE_ADMINISTER",
            status__in=["ORDERED", "PARTIALLY_DONE"]).count(),
        "can_manage": Cap.PHARMACY_MANAGE in caps_for(request.user),
        "can_dispense": Cap.DISPENSE in caps_for(request.user),
    }

    # If requested via htmx, return only the results partial fragment
    if request.headers.get("HX-Request"):
        return render(request, "pharmacy/_dashboard_results.html", context)

    return render(request, "pharmacy/dashboard.html", context)

@requires(Cap.PHARMACY_MANAGE)
def medication_create_or_edit_view(request, medication_id=None):
    medication = get_object_or_404(Medication, pk=medication_id) if medication_id else None
    classes = AllergenClass.objects.order_by("name")
    stock = StockItem.objects.filter(medication=medication).first() if medication else None
    selected_ids = [str(c.id) for c in medication.allergen_classes.all()] if medication else []

    if request.method == "POST":
        fields = {k: request.POST.get(k, "").strip()
                  for k in ["brand_name", "generic_name", "form", "strength"]}
        try:
            price = Decimal(request.POST.get("unit_price", "").strip())
        except InvalidOperation:
            price = Decimal(-1)
        if not all(fields.values()) or price < 0:
            messages.error(request, "Fill in every field. The unit price must be zero or more.")
            return _render_form(request, medication, stock, classes, selected_ids)

        try:
            with transaction.atomic():
                if medication is None:
                    medication = Medication.objects.create(**fields, unit_price=price, is_active=True)
                    StockItem.objects.get_or_create(medication=medication,
                                                    defaults={"quantity": 0, "reorder_level": 50})
                    action = "created"
                else:
                    for k, v in fields.items():
                        setattr(medication, k, v)
                    medication.unit_price = price
                    medication.save()
                    action = "updated"
                medication.allergen_classes.set(
                    AllergenClass.objects.filter(id__in=request.POST.getlist("allergen_class_ids")))
        except IntegrityError:
            messages.error(request, "A medicine with this brand, generic, form and strength already exists.")
            return _render_form(request, medication, stock, classes, selected_ids)

        log_action(request, AuditAction.MEDICATION_CREATED if action == "created" else AuditAction.MEDICATION_UPDATED,
                   f"{action.title()} medicine {medication.brand_name} {medication.strength}.")
        messages.success(request, f"{medication.brand_name} saved.")
        return redirect("pharmacy:medication_edit", medication_id=medication.id)

    return _render_form(request, medication, stock, classes, selected_ids)


def _render_form(request, medication, stock, classes, selected_ids):
    return render(request, "pharmacy/medication_form.html", {
        "medication": medication, "stock": stock, "classes": classes, "selected_ids": selected_ids,
    })


@requires(Cap.PHARMACY_MANAGE)
@require_POST
def stock_receive_action(request, medication_id):
    medication = get_object_or_404(Medication, pk=medication_id)
    try:
        qty = _int(request.POST.get("quantity"), "Quantity")
        services.receive_stock(medication=medication, quantity=qty, staff=_profile(request))
    except PharmacyError as exc:
        messages.error(request, str(exc))
        return redirect("pharmacy:medication_edit", medication_id=medication.id)

    log_action(request, AuditAction.STOCK_RECEIVED,
               f"Received {qty} of {medication.brand_name} {medication.strength}.")
    messages.success(request, f"Received {qty} unit(s).")
    return redirect("pharmacy:medication_edit", medication_id=medication.id)


@requires(Cap.PHARMACY_MANAGE)
@require_POST
def stock_adjust_action(request, medication_id):
    medication = get_object_or_404(Medication, pk=medication_id)
    note = request.POST.get("note", "").strip()
    try:
        delta = _int(request.POST.get("delta"), "Adjustment")
        if not note:
            raise PharmacyError("Give a reason for the adjustment.")
        services.adjust_stock(medication=medication, delta=delta, staff=_profile(request))
    except PharmacyError as exc:
        messages.error(request, str(exc))
        return redirect("pharmacy:medication_edit", medication_id=medication.id)

    log_action(request, AuditAction.STOCK_ADJUSTED,
               f"Adjusted {medication.brand_name} by {delta:+d}: {note}")
    messages.success(request, f"Stock adjusted by {delta:+d}.")
    return redirect("pharmacy:medication_edit", medication_id=medication.id)


@requires(Cap.DISPENSE)
def dispense_queue_view(request):
    orders = (MedicationOrder.objects
              .filter(source="FORMULARY", fulfilment="PHARMACY_DISPENSE",
                      status__in=["ORDERED", "PARTIALLY_DONE"])
              .select_related("encounter__patient", "medication", "ordered_by__user")
              .prefetch_related("dispensations")
              .order_by("ordered_at"))
    return render(request, "pharmacy/dispense_queue.html", {"orders": _annotate(orders)})


@requires(Cap.DISPENSE)
@require_POST
def dispense_action(request, order_id):
    try:
        qty = _int(request.POST.get("quantity"), "Quantity")
        scanned = serial_rules.parse_list(request.POST.get("serials", ""))
        order = services.dispense(order_id=order_id, quantity=qty, staff=_profile(request),
                                  serials=scanned or None)
    except (PharmacyError, serial_rules.SerialError) as exc:
        messages.error(request, str(exc))
        return redirect("pharmacy:dispense_queue")

    log_action(request, AuditAction.MEDICATION_DISPENSED,
               f"Dispensed {qty} of {order.medication.brand_name} for {order.encounter.patient}.")
    messages.success(request, f"Dispensed {qty} of {order.medication.brand_name}.")
    return redirect("pharmacy:dispense_queue")


@requires(Cap.NURSE_ADMINISTER)
def nurse_worklist_view(request):
    orders_qs = (MedicationOrder.objects
                 .filter(source="FORMULARY", fulfilment="NURSE_ADMINISTER",
                         status__in=["ORDERED", "PARTIALLY_DONE"])
                 .select_related("encounter__patient", "medication", "ordered_by__user")
                 .prefetch_related("dispensations", "administrations")
                 .order_by("ordered_at"))
    
    paginator = Paginator(orders_qs, 30)
    page_obj = paginator.get_page(request.GET.get("page"))
    
    return render(request, "pharmacy/nurse_worklist.html", {
        "page_obj": page_obj,
        "orders": _annotate(page_obj.object_list),
    })


@requires(Cap.NURSE_ADMINISTER)
@require_POST
def administer_action(request, order_id):
    try:
        scanned = serial_rules.parse_list(request.POST.get("serials", ""))
        order = services.administer(
            order_id=order_id,
            nurse=_profile(request),
            dose_given_text=request.POST.get("dose_given_text", ""),
            site=request.POST.get("site", ""),
            notes=request.POST.get("notes", ""),
            serials=scanned or None,
        )
    except (PharmacyError, serial_rules.SerialError) as exc:
        messages.error(request, str(exc))
        return redirect("pharmacy:nurse_worklist")

    log_action(request, AuditAction.MEDICATION_ADMINISTERED,
               f"Administered {order.medication.brand_name} to {order.encounter.patient}.")
    messages.success(request, f"Recorded: {order.medication.brand_name} given.")
    return redirect("pharmacy:nurse_worklist")


@requires(Cap.PHARMACY_MANAGE)
def receive_view(request):
    medications = Medication.objects.filter(is_active=True).order_by("brand_name")
    if request.method == "POST":
        medication = get_object_or_404(Medication, pk=request.POST.get("medication"), is_active=True)
        try:
            expiry = date.fromisoformat(request.POST.get("expiry_date", "").strip())
            per_unit = int(request.POST.get("doses_per_unit") or medication.doses_per_unit)
            scanned = serial_rules.parse_list(request.POST.get("serials", ""))
            batch, units = services.receive_batch(
                medication=medication, batch_number=request.POST.get("batch_number", ""),
                expiry_date=expiry, serials=scanned, doses_per_unit=per_unit,
                staff=_profile(request),
            )
        except (PharmacyError, serial_rules.SerialError, ValueError) as exc:
            messages.error(request, str(exc) or "Check the receipt details.")
            return render(request, "pharmacy/receive.html", {"medications": medications, "form": request.POST})

        log_action(request, AuditAction.STOCK_BATCH_RECEIVED,
                   f"Received batch {batch.batch_number} of {medication.brand_name}: {len(units)} unit(s).")
        messages.success(request, f"Received {len(units)} unit(s) of batch {batch.batch_number}.")
        return redirect("pharmacy:units")
    return render(request, "pharmacy/receive.html", {"medications": medications, "form": {}})


@requires(Cap.VIEW_PHARMACY)
def units_view(request):
    query = request.GET.get("serial", "").strip().upper()
    units = StockUnit.objects.select_related("medication", "batch").order_by("batch__expiry_date", "serial")
    if query:
        units = units.filter(serial__icontains=query)
    page_obj = Paginator(units, 50).get_page(request.GET.get("page"))
    return render(request, "pharmacy/units.html", {
        "page_obj": page_obj, "query": query,
        "can_manage": Cap.PHARMACY_MANAGE in caps_for(request.user),
        "today": timezone.localdate(),
    })


@requires(Cap.PHARMACY_MANAGE)
@require_POST
def write_off_action(request, serial):
    try:
        unit = services.write_off(serial=serial, reason=request.POST.get("reason", ""),
                                  staff=_profile(request))
    except PharmacyError as exc:
        messages.error(request, str(exc))
        return redirect("pharmacy:units")
    log_action(request, AuditAction.STOCK_UNIT_WRITTEN_OFF,
               f"Wrote off {unit.serial} ({unit.medication.brand_name}): {request.POST.get('reason', '').strip()}")
    messages.success(request, f"{unit.serial} written off.")
    return redirect("pharmacy:units")
