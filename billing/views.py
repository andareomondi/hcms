from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from triage_queue.models import Encounter
from .models import Charge


def is_billing_officer(user):
    return hasattr(user, "staff_profile") and user.staff_profile.role in ("BILLING", "ADMIN")


@login_required
@user_passes_test(is_billing_officer)
def provisional_review_view(request):
    provisional_charges = Charge.objects.filter(status="PROVISIONAL").select_related(
        "billing_account__owner", "treated_patient", "encounter", "created_by__user"
    ).order_by("-created_at")
    return render(request, "billing/provisional_review.html", {"provisional_charges": provisional_charges})


@login_required
@user_passes_test(is_billing_officer)
@require_POST
@transaction.atomic
def post_charge_action(request, charge_id):
    charge = get_object_or_404(Charge.objects.select_for_update(), pk=charge_id)
    if charge.status != "PROVISIONAL":
        raise ValidationError("Only provisional charges can be posted.")
    if charge.created_by == request.staff_profile:
        raise PermissionDenied("Self-posting of charges is prohibited.")

    charge.status = "POSTED"
    charge.posted_at = timezone.now()
    charge.save(update_fields=["status", "posted_at"])
    return redirect("billing:provisional_review")


@login_required
@user_passes_test(is_billing_officer)
def invoice_detail_view(request, encounter_id):
    encounter = get_object_or_404(Encounter.objects.select_related("patient"), pk=encounter_id)
    charges = encounter.charges.filter(status="POSTED")
    total_amount = sum(c.amount for c in charges)
    return render(request, "billing/invoice.html", {"encounter": encounter, "charges": charges, "total_amount": total_amount})
