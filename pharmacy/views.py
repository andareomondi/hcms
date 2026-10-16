from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action

from .models import Medication, MedicationOrder, StockItem


@login_required
def pharmacy_dashboard_view(request):
    """Main pharmacy dashboard with search, pagination, percentages, and low stock cards."""
    query = request.GET.get("q", "").strip()

    medication_qs = Medication.objects.order_by("brand_name")
    if query:
        medication_qs = medication_qs.filter(
            Q(brand_name__icontains=query) | Q(generic_name__icontains=query)
        )

    # Pagination (15 items per page)
    paginator = Paginator(medication_qs, 15)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Map stock items for the current page
    stock_map = {
        item.medication_id: item
        for item in StockItem.objects.filter(medication__in=page_obj.object_list)
    }
    for med in page_obj.object_list:
        med.prefetched_stock = stock_map.get(med.id)

    # Metrics & Percentages
    total_meds_count = Medication.objects.count() or 1
    low_stock_items_qs = StockItem.objects.filter(quantity__lte=50).select_related("medication")
    low_stock_count = low_stock_items_qs.count()
    adequate_stock_count = max(0, total_meds_count - low_stock_count)
    stock_health_pct = int((adequate_stock_count / total_meds_count) * 100)

    pending_orders_count = MedicationOrder.objects.filter(status="ORDERED").count()

    context = {
        "page_obj": page_obj,
        "medications": page_obj.object_list,
        "query": query,
        "low_stock_items": low_stock_items_qs[:6],  # Top low stock items for alert cards
        "low_stock_count": low_stock_count,
        "stock_health_pct": stock_health_pct,
        "pending_orders_count": pending_orders_count,
        "total_meds_count": total_meds_count,
    }
    return render(request, "pharmacy/dashboard.html", context)


@login_required
def medication_create_or_edit_view(request, medication_id=None):
    """Add a new drug to the formulary or edit existing drug and stock."""
    medication = get_object_or_404(Medication, pk=medication_id) if medication_id else None
    stock_item = StockItem.objects.filter(medication=medication).first() if medication else None

    if request.method == "POST":
        brand_name = request.POST.get("brand_name", "").strip()
        generic_name = request.POST.get("generic_name", "").strip()
        form = request.POST.get("form", "").strip()
        strength = request.POST.get("strength", "").strip()
        unit_price = request.POST.get("unit_price", 0.0)
        quantity = int(request.POST.get("quantity", 0))

        if medication:
            medication.brand_name = brand_name
            medication.generic_name = generic_name
            medication.form = form
            medication.strength = strength
            medication.unit_price = unit_price
            medication.save()
            log_action(
                request,
                AuditAction.MEDICATION_UPDATED,
                f"Updated medication #{medication.id} {medication.brand_name} {medication.strength}.",
            )
        else:
            medication = Medication.objects.create(
                brand_name=brand_name,
                generic_name=generic_name,
                form=form,
                strength=strength,
                unit_price=unit_price,
                is_active=True,
            )
            log_action(
                request,
                AuditAction.MEDICATION_CREATED,
                f"Added medication #{medication.id} {medication.brand_name} {medication.strength}.",
            )

        if stock_item:
            previous_quantity = stock_item.quantity
            stock_item.quantity = quantity
            stock_item.save()
            if previous_quantity != quantity:
                log_action(
                    request,
                    AuditAction.STOCK_ADJUSTED,
                    f"Adjusted stock of {medication.brand_name} from {previous_quantity} to {quantity}.",
                )
        else:
            StockItem.objects.create(medication=medication, quantity=quantity, reorder_level=50)
            log_action(
                request,
                AuditAction.STOCK_ADJUSTED,
                f"Set initial stock of {medication.brand_name} to {quantity}.",
            )

        messages.success(request, f"Medication '{medication.brand_name}' saved successfully.")
        return redirect("pharmacy:dashboard")

    return render(request, "pharmacy/medication_form.html", {"medication": medication, "stock_item": stock_item})


@login_required
def nurse_worklist_view(request):
    """Nurse administration worklist for active prescribed orders."""
    orders = MedicationOrder.objects.filter(status="ORDERED").select_related(
        "encounter__patient", "medication", "ordered_by__user"
    ).order_by("-ordered_at")[:50]

    return render(request, "pharmacy/nurse_worklist.html", {"orders": orders})


@login_required
@require_POST
def mark_administered_action(request, order_id):
    order = get_object_or_404(
        MedicationOrder.objects.select_related("medication", "encounter__patient"),
        pk=order_id,
    )
    order.status = "DISPENSED"
    order.save(update_fields=["status"])

    stock = StockItem.objects.filter(medication=order.medication).first()
    if stock and stock.quantity > 0:
        stock.quantity -= 1
        stock.save(update_fields=["quantity"])

    log_action(
        request,
        AuditAction.MEDICATION_ADMINISTERED,
        f"Administered {order.medication.brand_name} (order #{order.id}) to patient {order.encounter.patient}.",
    )

    messages.success(request, f"Marked {order.medication.brand_name} as administered.")
    return redirect("pharmacy:nurse_worklist")
