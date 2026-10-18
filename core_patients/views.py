from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing.models import Charge
from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from dashboard.views import is_system_admin
from wards.models import Admission

from .models import (
    Allergen,
    Department,
    Patient,
    PatientAllergy,
    StaffProfile,
    StaffRegistrationRequest,
)
from .ratelimit import too_many_attempts


def staff_register_view(request):
    """
    PUBLIC. Submits a staff account request for administrator review.
    Creates no User and does not log anyone in.
    """
    if request.method == "POST":
        # Honeypot: real users never fill this hidden field. Bots do.
        if request.POST.get("website"):
            return render(request, "core_patients/staff_register_submitted.html",
                          {"username": "your request"})
        if too_many_attempts(request.META.get("REMOTE_ADDR", "unknown")):
            return HttpResponse("Too many requests. Please try again later.", status=429)

    departments = Department.objects.all()
    role_choices = [r for r in StaffProfile.ROLE_CHOICES
                    if r[0] in StaffRegistrationRequest.REQUESTABLE_ROLES]
    context = {"departments": departments, "roles": role_choices}

    if request.method == "GET":
        return render(request, "core_patients/staff_register.html", context)

    fields = {k: request.POST.get(k, "").strip()
              for k in ["username", "email", "first_name", "last_name",
                        "employee_id", "role", "department_id"]}
    password = request.POST.get("password", "")
    error = None

    if not all([fields["username"], fields["email"], fields["employee_id"],
                fields["role"], fields["department_id"], password]):
        error = "All fields are required."
    elif fields["role"] not in StaffRegistrationRequest.REQUESTABLE_ROLES:
        error = "That role cannot be requested. Contact an administrator."
    elif User.objects.filter(username=fields["username"]).exists() or \
            StaffRegistrationRequest.objects.filter(username=fields["username"], status="PENDING").exists():
        error = "That username is taken or already awaiting approval."
    elif StaffProfile.objects.filter(employee_id=fields["employee_id"]).exists() or \
            StaffRegistrationRequest.objects.filter(employee_id=fields["employee_id"], status="PENDING").exists():
        error = "That employee ID is already in use or awaiting approval."
    else:
        department = Department.objects.filter(pk=fields["department_id"]).first() \
            if _is_uuid(fields["department_id"]) else None
        if department is None:
            error = "Select a valid department."
        else:
            try:
                validate_password(password)
            except ValidationError as exc:
                error = " ".join(exc.messages)

    if error:
        context.update({"error": error, "form_values": fields})
        return render(request, "core_patients/staff_register.html", context)

    try:
        with transaction.atomic():
            StaffRegistrationRequest.objects.create(
                username=fields["username"],
                email=fields["email"],
                first_name=fields["first_name"],
                last_name=fields["last_name"],
                employee_id=fields["employee_id"],
                requested_role=fields["role"],
                department=department,
                password_hash=make_password(password),
                submitted_ip=request.META.get("REMOTE_ADDR"),
            )
    except IntegrityError:
        context.update({"error": "A request with these details was just submitted. Try different values."})
        return render(request, "core_patients/staff_register.html", context)

    return render(request, "core_patients/staff_register_submitted.html",
                  {"username": fields["username"]})


def _is_uuid(value):
    import uuid
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False

@login_required
@user_passes_test(is_system_admin, login_url="/accounts/login/")
def staff_requests_view(request):
    """Administrator queue of staff account requests, filterable by status."""
    status = request.GET.get("status", "PENDING")
    if status not in ("PENDING", "APPROVED", "DECLINED"):
        status = "PENDING"
    requests_qs = StaffRegistrationRequest.objects.select_related("department").filter(status=status)[:200]
    counts = {
        s: StaffRegistrationRequest.objects.filter(status=s).count()
        for s in ("PENDING", "APPROVED", "DECLINED")
    }
    return render(request, "core_patients/staff_requests.html", {
        "requests": requests_qs, "status": status, "counts": counts,
    })


@login_required
@user_passes_test(is_system_admin, login_url="/accounts/login/")
@require_POST
def staff_request_approve_view(request, request_id):
    with transaction.atomic():
        # Lock the request row so two administrators cannot approve it twice
        req = StaffRegistrationRequest.objects.select_for_update().get(pk=request_id)
        if req.status != "PENDING":
            messages.error(request, "This request has already been reviewed.")
            return redirect("core_patients:staff_requests")

        user = User(
            username=req.username,
            email=req.email,
            first_name=req.first_name,
            last_name=req.last_name,
            is_active=True,
        )
        user.password = req.password_hash  # already hashed at submission time
        user.save()

        StaffProfile.objects.create(
            user=user,
            employee_id=req.employee_id,
            role=req.requested_role,
            department=req.department,
            is_active=True,
        )

        req.status = "APPROVED"
        req.reviewed_by = request.user
        req.reviewed_at = timezone.now()
        req.created_user = user
        req.password_hash = ""  # no longer needed
        req.save()

        log_action(request, AuditAction.STAFF_REQUEST_APPROVED,
                   f"Approved staff request '{req.username}' as {req.get_requested_role_display()} "
                   f"in {req.department}.")

    messages.success(request, f"Approved {req.username}. They can now log in.")
    return redirect("core_patients:staff_requests")


@login_required
@user_passes_test(is_system_admin, login_url="/accounts/login/")
@require_POST
def staff_request_decline_view(request, request_id):
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "A reason is required to decline a request.")
        return redirect("core_patients:staff_requests")

    with transaction.atomic():
        req = StaffRegistrationRequest.objects.select_for_update().get(pk=request_id)
        if req.status != "PENDING":
            messages.error(request, "This request has already been reviewed.")
            return redirect("core_patients:staff_requests")

        req.status = "DECLINED"
        req.reviewed_by = request.user
        req.reviewed_at = timezone.now()
        req.decline_reason = reason
        req.password_hash = ""
        req.save()

        log_action(request, AuditAction.STAFF_REQUEST_DECLINED,
                   f"Declined staff request '{req.username}': {reason}")

    messages.success(request, f"Declined {req.username}.")
    return redirect("core_patients:staff_requests")



@login_required
def patient_registry_search(request):
    """
    Live search endpoint triggered via htmx.
    Shows recent patients by default when query is empty,
    and filters dynamically as the user types.
    """
    query = request.GET.get("q", "").strip()

    # Start with all patients, ordered by newest first
    patients = Patient.objects.select_related("created_by").order_by("-created_at")

    # Apply filters only if a search query is provided
    if query:
        patients = patients.filter(
            Q(last_name__icontains=query) |
            Q(first_name__icontains=query) |
            Q(id_number__icontains=query)
        )

    # Cap results to prevent heavy DOM rendering loads
    patients = patients[:50]

    context = {"patients": patients, "query": query}

    # If requested via htmx, return only the table fragment
    if request.headers.get("HX-Request"):
        return render(request, "core_patients/_registry_results.html", context)

    # Otherwise, render the full registry page with the initial/filtered records
    return render(request, "core_patients/registry.html", context)


@login_required
def patient_register_view(request):
    """Handles new patient registration and emergency provisional creation."""
    if request.method == "POST":
        is_provisional = request.POST.get("is_provisional") == "true"
        last_name = request.POST.get("last_name", "").strip()

        if not is_provisional and not last_name:
            return render(request, "core_patients/register.html", {"error": "Last name is required."})

        patient = Patient.objects.create(
            first_name=request.POST.get("first_name", "").strip(),
            last_name=last_name or "Unidentified",
            date_of_birth=request.POST.get("date_of_birth") or None,
            sex=request.POST.get("sex", "U"),
            id_type=request.POST.get("id_type", "NONE"),
            id_number=request.POST.get("id_number") or None,
            is_provisional=is_provisional,
            provisional_label=request.POST.get("provisional_label", ""),
            phone=request.POST.get("phone", "").strip(),
            created_by=request.staff_profile,
        )

        status = "provisional" if is_provisional else "registered"
        log_action(
            request,
            AuditAction.PATIENT_REGISTERED,
            f"Registered {status} patient #{patient.id}: {patient}.",
        )
        return redirect("core_patients:detail", patient_id=patient.id)

    return render(request, "core_patients/register.html")


@login_required
def patient_detail_view(request, patient_id):
    """Comprehensive patient detail view showing demographics, ward admissions, duration, and financial ledgers."""
    patient = get_object_or_404(Patient.objects.select_related("created_by"), pk=patient_id)

    log_action(
        request,
        AuditAction.PATIENT_VIEWED,
        f"Viewed record of patient #{patient.id}: {patient}.",
    )

    patient_allergies = PatientAllergy.objects.filter(
        patient=patient, status="ACTIVE"
    ).select_related("allergen", "allergen__allergen_class")

    # Ward Admissions & Bed Allocations
    admissions = Admission.objects.filter(encounter__patient=patient).select_related("bed__ward", "admitted_by").order_by("-admitted_at")

    # Financial & Billing Data
    billing_account = getattr(patient, "billing_account", None)
    charges = Charge.objects.filter(
        Q(treated_patient=patient) | Q(encounter__patient=patient)
    ).order_by("-created_at")
    
    # Financial Metrics
    total_billed = sum(c.amount for c in charges)
    total_paid = sum(c.amount for c in charges.filter(status="PAID"))
    total_posted = sum(c.amount for c in charges.filter(status="POSTED"))
    total_provisional = sum(c.amount for c in charges.filter(status="PROVISIONAL"))

    # Clinical Timeline Items
    timeline_items = []
    for encounter in patient.encounters.all():
        for note in encounter.clinical_notes.all():
            timeline_items.append({
                "type": "NOTE", "get_type_display": "Clinical Note", "timestamp": note.created_at,
                "summary": f"Complaint: {note.presenting_complaint[:80]}... | Diagnosis: {note.diagnosis}", "author": note.author,
            })
        for order in encounter.medication_orders.all():
            timeline_items.append({
                "type": "ORDER", "get_type_display": "Medication Order", "timestamp": order.ordered_at,
                "summary": f"{order.medication.brand_name} {order.dose_text} - {order.get_route_display()}", "author": order.ordered_by,
            })

    timeline_items.sort(key=lambda x: x["timestamp"], reverse=True)

    context = {
        "patient": patient,
        "patient_allergies": patient_allergies,
        "admissions": admissions,
        "billing_account": billing_account,
        "charges": charges,
        "total_billed": total_billed,
        "total_paid": total_paid,
        "total_posted": total_posted,
        "total_provisional": total_provisional,
        "timeline_items": timeline_items,
    }
    return render(request, "core_patients/patient_detail.html", context)


@login_required
def patient_edit_view(request, patient_id):
    """Renders and processes patient demographic edits."""
    patient = get_object_or_404(Patient, pk=patient_id)
    if request.method == "POST":
        patient.first_name = request.POST.get("first_name", "").strip()
        patient.last_name = request.POST.get("last_name", "").strip()
        patient.phone = request.POST.get("phone", "").strip()
        patient.save()

        log_action(
            request,
            AuditAction.PATIENT_UPDATED,
            f"Updated demographics of patient #{patient.id}: {patient}.",
        )
        return redirect("core_patients:detail", patient_id=patient.id)
    return render(request, "core_patients/edit.html", {"patient": patient})


@login_required
def patient_timeline_view(request, patient_id):
    """htmx endpoint for infinite scrolling or refreshing timeline items."""
    patient = get_object_or_404(Patient, pk=patient_id)
    return render(request, "core_patients/_timeline_items.html", {"patient": patient})


@login_required
def allergy_panel_view(request, patient_id):
    """Renders the allergy management modal or panel."""
    patient = get_object_or_404(Patient, pk=patient_id)

    log_action(
        request,
        AuditAction.PATIENT_ALLERGIES_VIEWED,
        f"Viewed allergy panel for patient #{patient.id}: {patient}.",
    )

    allergies = patient.allergies.select_related("allergen").all()
    all_allergens = Allergen.objects.filter(is_active=True)
    context = {"patient": patient, "allergies": allergies, "all_allergens": all_allergens}
    return render(request, "core_patients/_allergy_panel.html", context)


@login_required
@require_POST
def confirm_identity_view(request, patient_id):
    """Clears provisional status after identity verification."""
    if not request.user.staff_profile.role in ("RECEPTION", "ADMIN", "DOCTOR"):
        return HttpResponseForbidden("Not authorized to confirm patient identities.")

    patient = get_object_or_404(Patient, pk=patient_id)
    patient.is_provisional = False
    patient.provisional_label = ""
    patient.id_type = request.POST.get("id_type", patient.id_type)
    patient.id_number = request.POST.get("id_number", patient.id_number)
    patient.save()

    log_action(
        request,
        AuditAction.PATIENT_IDENTITY_CONFIRMED,
        f"Confirmed identity of patient #{patient.id}: {patient} ({patient.id_type}).",
    )
    return redirect("core_patients:detail", patient_id=patient.id)
