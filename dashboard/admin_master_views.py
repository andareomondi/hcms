"""
System administrator master panel: overview KPIs and charts, patient list and editing,
rooms and duty, staff activation, audit stream. Ward and bed actions stay in dashboard/views.py.
"""
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.forms import modelform_factory
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing.models import Charge, Payment
from surgery import analytics as surgery_analytics
from surgery.models import SurgicalProcedure
from core_patients.models import Patient, StaffProfile
from duty.models import DutySession, Room
from triage_queue.models import Encounter
from wards.models import Bed, Ward

from .admin_crud import _get_obj, _StyledForm, superuser_required
from .audit_actions import AuditAction
from .export import finance_daily, metrics_daily
from .models import AuditLog
from .utils import log_action

WINDOW_DAYS = 30
PATIENTS_PER_PAGE = 30
PARTIAL_HEADER = "X-Partial"
ROOM_FIELDS = ["department", "code", "name", "floor", "is_active"]


def _overview(today):
    """KPI figures and chart series. Every number is read from the database."""
    start = today - timedelta(days=WINDOW_DAYS - 1)
    ops = metrics_daily(start, today)
    fin = finance_daily(start, today)

    billed = Charge.objects.exclude(status="VOIDED").aggregate(t=Sum("amount"))["t"] or Decimal(0)
    paid = Payment.objects.aggregate(t=Sum("amount"))["t"] or Decimal(0)

    kpis = {
        "patients": Patient.objects.count(),
        "visits_today": Encounter.objects.filter(opened_at__date=today).count(),
        "beds_total": Bed.objects.count(),
        "beds_vacant": Bed.objects.filter(status="VACANT").count(),
        "staff_active": StaffProfile.objects.filter(is_active=True).count(),
        "outstanding": float(billed - paid),
    }

    charts = {
        "labels": [r["date"] for r in ops],
        "patients": [r["patients_registered"] for r in ops],
        "visits": [r["visits_opened"] for r in ops],
        "admissions": [r["admissions"] for r in ops],
        "charges": [float(r["charges_posted_kes"]) for r in fin],
        "payments_mpesa": [float(r["payments_mpesa_kes"]) for r in fin],
        "payments_manual": [float(r["payments_manual_kes"]) for r in fin],
        "bed_status": list(Bed.objects.values("status").annotate(n=Count("id")).order_by("status")),
        "staff_roles": list(StaffProfile.objects.filter(is_active=True)
                            .values("role").annotate(n=Count("id")).order_by("role")),
        "duty_roles": list(StaffProfile.objects.filter(
                                id__in=DutySession.objects.filter(clocked_out_at__isnull=True)
                                .values("staff_id"))
                           .values("role").annotate(n=Count("id")).order_by("role")),
    }
    return kpis, charts


def _rooms_context():
    occupied_ids = set(DutySession.objects.filter(clocked_out_at__isnull=True, room__isnull=False)
                       .values_list("room_id", flat=True))
    room_rows = [{"room": r, "occupied": r.pk in occupied_ids}
                 for r in Room.objects.select_related("department").order_by("code")]
    on_duty = (DutySession.objects.select_related("staff__user", "room")
               .filter(clocked_out_at__isnull=True)
               .order_by("staff__user__last_name", "staff__user__username"))
    return {"room_rows": room_rows, "on_duty": list(on_duty)}


def _room_form():
    return modelform_factory(Room, form=_StyledForm, fields=ROOM_FIELDS)


def _patient_form():
    """Every editable concrete field on Patient. Auto and read-only fields are left out."""
    fields = [f.name for f in Patient._meta.concrete_fields if f.editable and not f.primary_key]
    return modelform_factory(Patient, form=_StyledForm, fields=fields)


def _patient_queryset(q):
    """
    All patients, newest first. With a term, matches case-insensitively
    across every text field on Patient.
    """
    qs = Patient.objects.all()
    if q:
        text_fields = [f.name for f in Patient._meta.concrete_fields
                       if f.get_internal_type() in ("CharField", "TextField")]
        cond = Q()
        for name in text_fields:
            cond |= Q(**{f"{name}__icontains": q})
        qs = qs.filter(cond)
    return qs.order_by("-created_at", "pk")


def _patient_context(request):
    q = request.GET.get("q", "").strip()
    paginator = Paginator(_patient_queryset(q), PATIENTS_PER_PAGE)
    page = paginator.get_page(request.GET.get("patient_page"))
    return {
        "q": q,
        "q_param": urlencode({"tab": "patients", "q": q}) if q else "tab=patients",
        "page": page,
    }


SURGERY_PROCEDURE_FIELDS = ["name", "category", "price", "requires_guardian_consent", "is_active"]


class _ProcedureForm(_StyledForm):
    def clean_price(self):
        price = self.cleaned_data.get("price")
        if price is not None and price < 0:
            raise forms.ValidationError("The fee cannot be negative.")
        return price


def _procedure_form():
    return modelform_factory(SurgicalProcedure, form=_ProcedureForm, fields=SURGERY_PROCEDURE_FIELDS)


def _surgery_context(today):
    procedures = SurgicalProcedure.objects.annotate(case_count=Count("cases")).order_by("name")
    return {
        "surgery_procedures": procedures,
        "surgery_form": _procedure_form()(),
        "surgery_summary": surgery_analytics.summary(today),
        "surgery_chart": surgery_analytics.chart_series(today, WINDOW_DAYS),
        "surgery_top": surgery_analytics.top_procedures(today.replace(day=1)),
    }


@superuser_required
def admin_master_view(request):
    # Search-as-you-type asks only for the patient results block.
    if request.headers.get(PARTIAL_HEADER) == "patients":
        return render(request, "dashboard/_admin_patient_results.html", _patient_context(request))

    tab = request.GET.get("tab", "overview")
    today = timezone.localdate()

    kpis, charts = _overview(today)

    wards = Ward.objects.prefetch_related("beds").order_by("name")

    staff_qs = StaffProfile.objects.select_related("user").order_by("user__username")
    staff_page = Paginator(staff_qs, 30).get_page(request.GET.get("staff_page"))

    audit_qs = (AuditLog.objects.select_related("actor")
                .filter(actor__isnull=False).exclude(actor__username="")
                .order_by("-timestamp"))
    audit_page = Paginator(audit_qs, 30).get_page(request.GET.get("audit_page"))

    context = {
        "active_tab": tab,
        "kpis": kpis,
        "chart_data": charts,
        "wards": wards,
        "staff_page": staff_page,
        "staff_members": staff_page.object_list,
        "audit_page": audit_page,
        "audit_logs": audit_page.object_list,
    }
    context.update(_rooms_context())
    context.update(_patient_context(request))
    context.update(_surgery_context(today))
    return render(request, "dashboard/admin_master.html", context)


@superuser_required
def admin_room_edit_view(request, pk=None):
    room = _get_obj(Room, pk) if pk else None
    Form = _room_form()

    if request.method == "POST":
        form = Form(request.POST, instance=room)
        if form.is_valid():
            changed = list(form.changed_data)
            with transaction.atomic():
                obj = form.save()
                if room is None:
                    log_action(request, AuditAction.CREATE, f"Admin created room {obj.code}.")
                    messages.success(request, f"Room {obj.code} created.")
                else:
                    log_action(request, AuditAction.UPDATE,
                               f"Admin updated room {obj.code}: {', '.join(changed) or 'no fields'}.")
                    messages.success(request, f"Room {obj.code} saved.")
            return redirect(f"{reverse('dashboard:admin_master')}?tab=rooms")
    else:
        form = Form(instance=room)

    return render(request, "dashboard/admin_room_edit.html", {"form": form, "room": room})


@superuser_required
@require_POST
def admin_room_toggle_action(request, room_id):
    room = _get_obj(Room, room_id)
    if room.is_active and room.duty_sessions.filter(clocked_out_at__isnull=True).exists():
        messages.error(request, f"{room.code} has someone clocked in. They must clock out first.")
    else:
        room.is_active = not room.is_active
        room.save(update_fields=["is_active"])
        state = "reopened" if room.is_active else "closed"
        log_action(request, AuditAction.UPDATE, f"Admin {state} room {room.code}.")
        messages.success(request, f"Room {room.code} {state}.")
    return redirect(f"{reverse('dashboard:admin_master')}?tab=rooms")


@superuser_required
def admin_patient_edit_view(request, pk):
    patient = _get_obj(Patient, pk)
    Form = _patient_form()

    if request.method == "POST":
        form = Form(request.POST, instance=patient)
        if form.is_valid():
            changed = list(form.changed_data)
            if changed:
                with transaction.atomic():
                    form.save()
                    # Field names only. Patient values stay out of the audit log.
                    log_action(request, AuditAction.PATIENT_UPDATED,
                               f"Admin updated patient {patient.pk}: {', '.join(changed)}.")
                messages.success(request, "Patient details saved.")
            else:
                messages.info(request, "No changes to save.")
            return redirect("dashboard:admin_patient_edit", pk=patient.pk)
    else:
        form = Form(instance=patient)
        log_action(request, AuditAction.PATIENT_VIEWED,
                   f"Admin opened patient {patient.pk} for editing.")

    return render(request, "dashboard/admin_patient_edit.html", {"form": form, "patient": patient})


@superuser_required
@require_POST
def admin_toggle_staff_action(request, staff_id):
    staff = _get_obj(StaffProfile, staff_id)
    if staff.user_id == request.user.pk:
        messages.error(request, "You cannot deactivate your own staff profile.")
    else:
        staff.is_active = not staff.is_active
        staff.save(update_fields=["is_active"])
        state = "activated" if staff.is_active else "deactivated"
        log_action(request, AuditAction.PROFILE_UPDATED,
                   f"Admin {state} staff profile {staff.employee_id or staff.pk} "
                   f"(user {staff.user.username}).")
        messages.success(request, f"Staff profile {state}.")
    return redirect(f"{reverse('dashboard:admin_master')}?tab=staff")


@superuser_required
@require_POST
def admin_surgery_procedure_create_action(request):
    form = _procedure_form()(request.POST)
    if form.is_valid():
        with transaction.atomic():
            obj = form.save()
            log_action(request, AuditAction.CREATE,
                       f"Admin added surgical procedure {obj.name} ({obj.get_category_display()}).")
        messages.success(request, f"{obj.name} added to the procedures the hospital offers.")
    else:
        problems = "; ".join(e for errs in form.errors.values() for e in errs)
        messages.error(request, f"The procedure was not saved. {problems}")
    return redirect(f"{reverse('dashboard:admin_master')}?tab=surgery")


@superuser_required
@require_POST
def admin_surgery_procedure_toggle_action(request, procedure_id):
    procedure = _get_obj(SurgicalProcedure, procedure_id)
    procedure.is_active = not procedure.is_active
    procedure.save(update_fields=["is_active"])
    state = "offered again" if procedure.is_active else "withdrawn from booking"
    log_action(request, AuditAction.UPDATE, f"Admin changed {procedure.name}: {state}.")
    messages.success(request, f"{procedure.name} is {state}.")
    return redirect(f"{reverse('dashboard:admin_master')}?tab=surgery")


@superuser_required
@require_POST
def admin_surgery_procedure_delete_action(request, procedure_id):
    procedure = _get_obj(SurgicalProcedure, procedure_id)
    name = procedure.name
    if procedure.cases.exists():
        messages.error(request, f"{name} has cases on record, so it cannot be deleted. Withdraw it from booking instead.")
    else:
        with transaction.atomic():
            procedure.delete()
            log_action(request, AuditAction.DELETE, f"Admin deleted surgical procedure {name}.")
        messages.success(request, f"{name} deleted.")
    return redirect(f"{reverse('dashboard:admin_master')}?tab=surgery")
