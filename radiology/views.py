from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import ScanOrder


@login_required
def radiology_worklist_view(request):
    orders = ScanOrder.objects.filter(status="ORDERED").select_related("encounter__patient", "ordered_by__user")
    return render(request, "radiology/worklist.html", {"orders": orders})


@login_required
@user_passes_test(lambda u: hasattr(u, "staff_profile") and u.staff_profile.role in ("RADIOLOGIST", "ADMIN"))
@require_POST
def save_radiology_report(request, order_id):
    order = get_object_or_404(ScanOrder, pk=order_id)
    order.status = "REPORTED"
    order.save(update_fields=["status"])
    return redirect("radiology:worklist")
