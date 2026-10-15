from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .models import Medication, MedicationOrder, NursingAdministration


@login_required
def medication_search_view(request):
    query = request.GET.get("q", "").strip()
    medications = Medication.objects.filter(brand_name__icontains=query, is_active=True)[:20] if len(query) >= 2 else []
    return render(request, "pharmacy/medication_search.html", {"medications": medications})


@login_required
def nurse_worklist_view(request):
    orders = MedicationOrder.objects.filter(fulfilment="NURSE_ADMINISTER", status="ORDERED").select_related("encounter__patient", "medication")
    return render(request, "pharmacy/nurse_worklist.html", {"orders": orders})


@login_required
def administer_modal_view(request, order_id):
    order = get_object_or_404(MedicationOrder, pk=order_id)
    return render(request, "pharmacy/_administer_modal.html", {"order": order})


@login_required
@require_POST
def record_administration_action(request, order_id):
    order = get_object_or_404(MedicationOrder, pk=order_id)
    NursingAdministration.objects.create(
        order=order,
        administered_by=request.staff_profile,
        dose_given_text=request.POST.get("dose_given_text", order.dose_text),
        site=request.POST.get("site", ""),
        notes=request.POST.get("notes", ""),
    )
    order.status = "COMPLETED"
    order.save(update_fields=["status"])
    return redirect("pharmacy:nurse_worklist")
