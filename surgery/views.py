"""Thin views. Every rule lives in services.py and clinical.py; these only read the form and show the result."""
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_POST

from billing import services as billing
from core_patients.models import Patient, StaffProfile
from hospital_system import validation as v
from hospital_system.permissions import Cap, has_any, requires
from triage_queue.models import Encounter

from . import clinical, services
from .models import (
    SurgicalCase, SurgicalComplication, SurgicalConsent, SurgicalProcedure, SurgicalTeamMember,
    TheatreStockBatch, TheatreStockItem,
)
from .services import SurgeryError

FAIL = (SurgeryError, v.ValidationError)


def _ip(request):
    return request.META.get("REMOTE_ADDR")


def _profile(request):
    return request.user.staff_profile


def _when(raw, label):
    """A required datetime from a form. Naive values are read in the local time zone."""
    value = parse_datetime((raw or "").strip())
    if value is None:
        raise SurgeryError(f"{label}: enter a date and time.")
    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    return value


@requires(Cap.SURGERY_VIEW)
def case_list_view(request):
    cases = SurgicalCase.objects.select_related("patient", "procedure").order_by("-booked_at")[:200]
    return render(request, "surgery/list.html", {"cases": cases})


@requires(Cap.SURGERY_BOOK)
def book_view(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    if request.method == "POST":
        try:
            procedure = get_object_or_404(SurgicalProcedure, pk=request.POST.get("procedure"), is_active=True)
            indication = None
            ind_id = request.POST.get("indication")
            if ind_id:
                indication = get_object_or_404(Encounter, pk=ind_id, patient=patient)
            scheduled = None
            if request.POST.get("scheduled_for"):
                scheduled = _when(request.POST.get("scheduled_for"), "Scheduled time")
            case = services.book_case(
                patient=patient, procedure=procedure, category=request.POST.get("category", ""),
                booked_by=_profile(request), indication_encounter=indication, scheduled_for=scheduled,
                allergy_clearance_note=request.POST.get("allergy_note", ""), ip=_ip(request))
        except FAIL as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Surgery booked. The fee is charged to its own billing container.")
            return redirect("surgery:case", case_id=case.pk)
    procedures = SurgicalProcedure.objects.filter(is_active=True)
    indications = Encounter.objects.filter(patient=patient).exclude(encounter_type="SURGERY").order_by("-opened_at")[:50]
    return render(request, "surgery/book.html", {
        "patient": patient, "procedures": procedures, "indications": indications,
        "categories": SurgicalCase.CATEGORY_CHOICES,
    })


@requires(Cap.SURGERY_VIEW)
def case_view(request, case_id):
    case = get_object_or_404(SurgicalCase.objects.select_related("patient", "procedure"), pk=case_id)
    theatre = _one(case, "theatre")
    pre_op = _one(case, "pre_op")
    consent = _one(case, "consent")
    sign_off = _one(case, "sign_off")
    team = {m.role: m for m in case.team.select_related("staff__user")}
    owed = clinical.outstanding_for_case(case)
    return render(request, "surgery/case.html", {
        "case": case, "theatre": theatre, "pre_op": pre_op, "consent": consent,
        "sign_off": sign_off, "team_rows": [(label, team.get(value)) for value, label in SurgicalTeamMember.ROLE_CHOICES],
        "team_roles": SurgicalTeamMember.ROLE_CHOICES,
        "consent_relationships": SurgicalConsent.RELATIONSHIP_CHOICES,
        "severity_choices": SurgicalComplication.SEVERITY_CHOICES,
        "can_book": _has(request, Cap.SURGERY_BOOK),
        "owed": owed, "age": clinical._age_years(case.patient),
        "anaesthetic_events": case.anaesthetic_events.order_by("given_at"),
        "recovery": case.recovery_observations.order_by("observed_at"),
        "complications": case.complications.order_by("occurred_at"),
        "stock_used": case.stock_used.select_related("batch__item").order_by("used_at"),
        "stock_items": TheatreStockItem.objects.filter(is_active=True),
        "staff_options": StaffProfile.objects.filter(is_active=True).select_related("user").order_by("user__username"),
        "can_clinical": _has(request, Cap.SURGERY_CLINICAL),
        "can_sign": _has(request, Cap.SURGERY_SIGN_OFF),
        "can_stock": _has(request, Cap.SURGERY_STOCK),
    })


def _one(obj, name):
    try:
        return getattr(obj, name)
    except ObjectDoesNotExist:
        return None


def _has(request, cap):
    return has_any(request.user, cap)


@require_POST
@requires(Cap.SURGERY_VIEW)
def case_action_view(request, case_id, action):
    case = get_object_or_404(SurgicalCase, pk=case_id)
    p, ip, post = _profile(request), _ip(request), request.POST
    needed = {
        "cancel": Cap.SURGERY_BOOK, "pre_op": Cap.SURGERY_CLINICAL, "consent": Cap.SURGERY_CLINICAL,
        "team": Cap.SURGERY_CLINICAL, "sign_in": Cap.SURGERY_CLINICAL, "time_out": Cap.SURGERY_CLINICAL,
        "incision": Cap.SURGERY_CLINICAL, "closure": Cap.SURGERY_CLINICAL, "sign_out": Cap.SURGERY_CLINICAL,
        "anaesthetic": Cap.SURGERY_CLINICAL, "recovery": Cap.SURGERY_CLINICAL,
        "complication": Cap.SURGERY_CLINICAL, "sign_surgeon": Cap.SURGERY_SIGN_OFF,
        "sign_anaesthetist": Cap.SURGERY_SIGN_OFF, "use_stock": Cap.SURGERY_STOCK,
    }.get(action)
    if needed is None or not _has(request, needed):
        messages.error(request, "You cannot do that here.")
        return redirect("surgery:case", case_id=case.pk)
    try:
        if action == "cancel":
            services.cancel_case(case=case, cancelled_by=p, reason=post.get("reason", ""), ip=ip)
        elif action == "pre_op":
            clinical.record_pre_op(case=case, staff=p, fasting_hours=post.get("fasting_hours"),
                                   vitals={k: post.get(k) for k in v_fields()}, ip=ip)
        elif action == "consent":
            clinical.record_consent(
                case=case, staff=p, signer_name=post.get("signer_name"),
                relationship=post.get("relationship"), is_guardian=bool(post.get("is_guardian")),
                typed_signature=post.get("typed_signature"), ip=ip)
        elif action == "team":
            member = get_object_or_404(StaffProfile, pk=post.get("staff"), is_active=True)
            clinical.add_team_member(case=case, staff_profile=member, role=post.get("role"), added_by=p, ip=ip)
        elif action == "sign_in":
            clinical.sign_in(case=case, staff=p, emergency_reason=post.get("emergency_reason", ""), ip=ip)
        elif action == "time_out":
            clinical.time_out(case=case, staff=p, note=post.get("note", ""), ip=ip)
        elif action == "incision":
            clinical.incision(case=case, staff=p, ip=ip)
        elif action == "closure":
            clinical.closure(case=case, staff=p, ip=ip)
        elif action == "sign_out":
            clinical.sign_out(case=case, staff=p, note=post.get("note", ""), ip=ip)
        elif action == "anaesthetic":
            clinical.record_anaesthetic(case=case, staff=p, given_at=_when(post.get("given_at"), "Given at"),
                                        agent=post.get("agent"), dose=post.get("dose"),
                                        route=post.get("route"), ip=ip)
        elif action == "recovery":
            clinical.record_recovery(case=case, staff=p, observed_at=_when(post.get("observed_at"), "Observed at"),
                                     vitals={k: post.get(k) for k in v_fields()},
                                     pain_score=post.get("pain_score") or None,
                                     notes=post.get("notes", ""), ip=ip)
        elif action == "complication":
            clinical.record_complication(case=case, staff=p, occurred_at=_when(post.get("occurred_at"), "Occurred at"),
                                         severity=post.get("severity"), description=post.get("description", ""), ip=ip)
        elif action == "sign_surgeon":
            clinical.surgeon_sign_off(case=case, staff=p, ip=ip)
        elif action == "sign_anaesthetist":
            clinical.anaesthetist_sign_off(case=case, staff=p, ip=ip)
        elif action == "use_stock":
            item = get_object_or_404(TheatreStockItem, pk=post.get("item"), is_active=True)
            clinical.use_stock(case=case, item=item, quantity=post.get("quantity"), staff=p, ip=ip)
        messages.success(request, "Saved.")
    except FAIL as exc:
        messages.error(request, str(exc))
    return redirect("surgery:case", case_id=case.pk)


def v_fields():
    from triage_queue.intake import TRIAGE_VITAL_RULES
    return list(TRIAGE_VITAL_RULES.keys()) + ["notes"]


@requires(Cap.SURGERY_STOCK)
def stock_view(request):
    if request.method == "POST":
        try:
            action = request.POST.get("action")
            if action == "item":
                name = v.text(request.POST.get("name"), "Item name", max_length=120, required=True)
                TheatreStockItem.objects.get_or_create(
                    name=name, defaults={"unit": v.text(request.POST.get("unit") or "each", "Unit", max_length=30)})
                messages.success(request, "Item added.")
            elif action == "receive":
                item = get_object_or_404(TheatreStockItem, pk=request.POST.get("item"), is_active=True)
                expiry = parse_date(request.POST.get("expiry_date", ""))
                clinical.receive_stock(item=item, batch_number=request.POST.get("batch_number"),
                                       expiry_date=expiry, quantity=request.POST.get("quantity"),
                                       staff=_profile(request), ip=_ip(request))
                messages.success(request, "Stock received.")
        except FAIL as exc:
            messages.error(request, str(exc))
        return redirect("surgery:stock")
    batches = TheatreStockBatch.objects.select_related("item").filter(quantity_on_hand__gt=0)
    return render(request, "surgery/stock.html", {
        "items": TheatreStockItem.objects.all(), "batches": batches,
    })
