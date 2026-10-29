from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing import services as billing_services
from billing.prices import SCAN_PRICES
from core_patients.allergy_services import conflicts_for
from core_patients.consent import can_start_consultation
from core_patients.models import PatientAllergy
from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from duty.services import current_session
from lab.models import LabTest
from notifications.services import notify_on_duty
from pharmacy import services as pharmacy_services
from pharmacy.models import Medication, MedicationOrder
from pharmacy.services import PharmacyError, StockShortError
from radiology.models import ScanOrder
from radiology.services import scan_cancellable, scan_charge_or_none
from triage_queue.models import Encounter, QueueEntry
from triage_queue.services import (
    FlowError,
    close_consultation,
    consultation_mode,
    pause_for_results,
    require_active,
    resume_consultation,
)
from wards import services as ward_services
from wards.models import Admission, Ward

from .models import ClinicalNote


def is_physician(user):
    return hasattr(user, "staff_profile") and user.staff_profile.role in ("DOCTOR", "ADMIN")


@login_required
@user_passes_test(is_physician)
def formulary_search_view(request, encounter_id):
    get_object_or_404(Encounter, pk=encounter_id)
    q = request.GET.get("q", "").strip()
    results = []
    if len(q) >= 2:
        results = list(Medication.objects.filter(is_active=True)
                       .filter(Q(brand_name__icontains=q) | Q(generic_name__icontains=q))
                       .order_by("brand_name")[:20])
        for m in results:
            m.available = pharmacy_services.available_to_prescribe(m)
    return render(request, "doctors_portal/_formulary_results.html", {"results": results, "q": q})



@login_required
@user_passes_test(is_physician)
def consultation_room_view(request, encounter_id):
    """Doctor's consultation room: triage details, vitals, notes, orders, admission and bill."""
    encounter = get_object_or_404(
        Encounter.objects.select_related("patient", "opened_by"),
        pk=encounter_id,
    )
    patient = encounter.patient

    clinical_notes = encounter.clinical_notes.select_related("author").order_by("-created_at")
    medication_orders = (encounter.medication_orders
                         .select_related("medication", "ordered_by")
                         .order_by("-ordered_at"))
    active_admission = (Admission.objects.filter(encounter=encounter, discharged_at__isnull=True)
                        .select_related("bed__ward").first())
    wards = Ward.objects.prefetch_related("beds").all()

    latest_vitals = encounter.vitals.order_by("-recorded_at").first()
    vitals_history = encounter.vitals.order_by("-recorded_at")[:10]
    scan_orders = list(
        encounter.scan_orders
        .prefetch_related("studies")
        .order_by("-ordered_at")
    )
    for scan in scan_orders:
        studies = list(scan.studies.all())
        scan.latest_study = max(studies, key=lambda s: s.uploaded_at) if studies else None
        scan.can_cancel = scan_cancellable(scan)
    allergies = (PatientAllergy.objects
                 .filter(patient=patient, status="ACTIVE")
                 .select_related("allergen"))
    tally = billing_services.encounter_tally(encounter)
    mode, queue_entry = consultation_mode(encounter, request.staff_profile)
    prefill = request.session.pop("prefill_external", None)

    context = {
        "encounter": encounter,
        "patient": patient,
        "lab_tests": LabTest.objects.filter(is_active=True).order_by("section", "name"),
        "clinical_notes": clinical_notes,
        "medication_orders": medication_orders,
        "active_admission": active_admission,
        "wards": wards,
        "latest_vitals": latest_vitals,
        "vitals_history": vitals_history,
        "prefill": prefill,
        "scan_orders": scan_orders,
        "allergies": allergies,
        "tally": tally,
        "scan_prices": SCAN_PRICES,
        "mode": mode,
        "queue_entry": queue_entry,
        "can_act": mode in ("ACTIVE", "IN_WARD"),
        "lab_orders": (encounter.lab_orders.prefetch_related("lines__test")
               .order_by("-ordered_at")),
    }
    return render(request, "doctors_portal/consultation_room.html", context)


@login_required
@user_passes_test(is_physician)
@require_POST
def add_clinical_note_action(request, encounter_id):
    """Saves clinical examination notes and diagnosis."""
    encounter = get_object_or_404(Encounter, pk=encounter_id)

    try:
        require_active(encounter, request.staff_profile)
    except FlowError as exc:
        messages.error(request, str(exc))
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)

    if request.staff_profile is None:
        messages.error(request, "Your account has no staff profile, so notes cannot be saved.")
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)

    complaint = request.POST.get("presenting_complaint", "").strip()
    examination = request.POST.get("examination_notes", "").strip()
    diagnosis = request.POST.get("diagnosis", "").strip()
    plan = request.POST.get("treatment_plan", "").strip()

    if complaint and diagnosis:
        ClinicalNote.objects.create(
            encounter=encounter,
            presenting_complaint=complaint,
            examination=examination,
            diagnosis=diagnosis,
            plan=plan,
            author=request.staff_profile,
        )
        log_action(request, AuditAction.CLINICAL_NOTE_SAVED,
                   f"Added clinical note & diagnosis for patient {encounter.patient}.")
        messages.success(request, "Clinical notes saved successfully.")
    else:
        messages.error(request, "Presenting complaint and diagnosis are required.")

    return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)


@login_required
@user_passes_test(is_physician)
@require_POST
def prescribe_medication_action(request, encounter_id):
    encounter = get_object_or_404(Encounter, pk=encounter_id)
    profile = request.staff_profile
    try:
        require_active(encounter, profile)
    except FlowError as exc:
        messages.error(request, str(exc))
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)

    source = request.POST.get("source", "")
    route = request.POST.get("route", "ORAL")
    frequency = request.POST.get("frequency", "OD")
    dose_text = request.POST.get("dose_text", "").strip()
    try:
        days = max(1, int(request.POST.get("duration_days", "5")))
    except ValueError:
        days = 5
    per_day = {"OD": 1, "BID": 2, "TID": 3, "QID": 4, "STAT": 1}.get(frequency, 1)
    doses_total = per_day * days if frequency != "STAT" else 1

    if source == "EXTERNAL":
        return _create_external(request, encounter, profile, route, frequency,
                                dose_text, days, doses_total)
    if source == "FORMULARY":
        return _create_formulary(request, encounter, profile, route, frequency,
                                 dose_text, days, doses_total)
    messages.error(request, "Choose hospital formulary or external purchase.")
    return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)



def _create_formulary(request, encounter, profile, route, frequency, dose_text, days, doses_total):
    medication = Medication.objects.filter(pk=request.POST.get("medication_id"), is_active=True).first()
    if medication is None:
        messages.error(request, "Search the formulary and select a medicine before prescribing.")
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)

    override = request.POST.get("allergy_override_reason", "").strip()
    hits = list(conflicts_for(encounter.patient, medication))
    if hits and not override:
        names = ", ".join(h.allergen.display_name for h in hits)
        messages.error(request,
                       f"Allergy alert: the patient is recorded as allergic to {names}. "
                       "Enter an override reason to prescribe anyway.")
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)

    try:
        order = pharmacy_services.create_formulary_order(
            encounter=encounter, medication=medication, doctor=profile,
            route=route, frequency=frequency, dose_text=dose_text, days=days,
            doses_total=doses_total, override=override if hits else "", staff=profile,
        )
    except StockShortError as exc:
        request.session["prefill_external"] = {
            "external_name": medication.brand_name,
            "external_strength": medication.strength,
            "external_form": medication.form,
            "external_reason": (f"Hospital stock not available at prescribing: {exc} "
                                f"Prescribed as external to avoid a delay."),
            "route": route,
            "frequency": frequency,
            "dose_text": dose_text,
            "duration_days": days,
        }
        messages.error(request,
                       f"{exc} The hospital cannot supply this prescription. The external prescription "
                       "is filled in below. Review it, confirm the allergy check, and issue it.")
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)

    if hits:
        names = ", ".join(h.allergen.display_name for h in hits)
        log_action(request, AuditAction.ALLERGY_OVERRIDDEN,
                   f"Prescribed {medication.brand_name} despite recorded allergy to {names} "
                   f"for {encounter.patient}. Reason: {override}")
    log_action(request, AuditAction.MEDICATION_ORDERED,
               f"Prescribed {medication.brand_name} for {encounter.patient} ({doses_total} dose(s)).")
    messages.success(request, f"Prescribed {medication.brand_name}.")
    return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)



def _create_external(request, encounter, profile, route, frequency, dose_text, days, doses_total):
    name = request.POST.get("external_name", "").strip()
    strength = request.POST.get("external_strength", "").strip()
    form = request.POST.get("external_form", "").strip()
    reason = request.POST.get("external_reason", "").strip()
    acknowledged = request.POST.get("allergy_check_acknowledged") == "1"

    if not name:
        messages.error(request, "Enter the medicine name.")
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)
    if not acknowledged:
        messages.error(request, "You must confirm the allergy check and accept responsibility "
                                "to issue an external prescription.")
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)

    matches = Medication.objects.filter(is_active=True).filter(
        Q(brand_name__iexact=name) | Q(generic_name__iexact=name)).exists()
    if matches and not reason:
        messages.error(request, f"'{name}' is in the hospital formulary. Give a reason to "
                                "prescribe it as external, or choose it from the formulary.")
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)

    now = timezone.now()
    with transaction.atomic():
        MedicationOrder.objects.create(
            encounter=encounter, ordered_by=profile, medication=None, source="EXTERNAL",
            external_name=name[:200], external_strength=strength[:60], external_form=form[:60],
            external_reason=reason,
            route=route, fulfilment="PHARMACY_DISPENSE" if route == "ORAL" else "NURSE_ADMINISTER",
            dose_text=dose_text or strength, frequency_text=frequency,
            duration_days=days, doses_total=doses_total, status="ORDERED",
            allergy_check_acknowledged=True,
            allergy_check_acknowledged_by=profile, allergy_check_acknowledged_at=now,
        )
    log_action(request, AuditAction.EXTERNAL_PRESCRIPTION_ACKNOWLEDGED,
               f"External prescription '{name}' for {encounter.patient}, acknowledged by {profile}. "
               f"Matched formulary: {matches}. Reason: {reason or 'none'}.")
    messages.success(request, f"External prescription recorded for {name}. Not billed by the hospital.")
    return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)


@login_required
@user_passes_test(is_physician)
@require_POST
def admit_patient_action(request, encounter_id):
    encounter = get_object_or_404(Encounter, pk=encounter_id)
    try:
        days = int(request.POST.get("planned_days", "1"))
        ward_services.admit(encounter=encounter, bed_id=request.POST.get("bed_id"),
                            planned_days=days, staff=request.staff_profile)
    except (ValueError, FlowError) as exc:
        messages.error(request, str(exc) if str(exc) else "Choose a vacant bed and a valid stay length.")
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)

    log_action(request, AuditAction.WARD_ADMISSION_CREATED,
               f"Admitted {encounter.patient} for {days} day(s).")
    messages.success(request, f"Admitted for {days} day(s). A bed-day charge was added.")
    return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)


@login_required
@user_passes_test(is_physician)
@require_POST
def complete_consultation_action(request, encounter_id):
    encounter = get_object_or_404(Encounter, pk=encounter_id)
    try:
        result = close_consultation(encounter, request.staff_profile)
    except FlowError as exc:
        messages.error(request, str(exc))
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)

    log_action(request, AuditAction.CONSULTATION_COMPLETED,
               f"Completed consultation for {encounter.patient} (admitted={result['admitted']}).")
    if result["admitted"]:
        messages.success(request, "Consultation complete. Patient remains in the ward.")
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)
    messages.success(request, "Visit closed. The bill has been sent to billing.")
    return redirect("doctors_portal:dashboard")


@login_required
@user_passes_test(is_physician)
def doctor_dashboard_view(request):
    doctor_profile = request.staff_profile
    session = current_session(doctor_profile)
    if session is None:
        messages.info(request, "Clock in with your room to see your patients.")
        return redirect("duty:clock_in")

    now = timezone.now()
    current_consultation = QueueEntry.objects.filter(
        assigned_doctor=doctor_profile, status="IN_CONSULTATION"
    ).select_related("encounter__patient", "encounter").first()

    waiting_entries = QueueEntry.objects.filter(
        assigned_doctor=doctor_profile, status="WAITING"
    ).select_related("encounter__patient", "encounter").order_by("-priority", "joined_at")
    for entry in waiting_entries:
        entry.wait_duration_minutes = int((now - entry.joined_at).total_seconds() / 60)

    awaiting_results = (QueueEntry.objects.filter(
        assigned_doctor=doctor_profile, status="AWAITING_RESULTS")
        .select_related("encounter__patient", "encounter")
        .prefetch_related("encounter__scan_orders")
        .order_by("joined_at"))

    context = {
        "session": session,
        "current_consultation": current_consultation,
        "waiting_entries": waiting_entries,
        "awaiting_results": awaiting_results,
    }
    return render(request, "doctors_portal/dashboard.html", context)


@login_required
@user_passes_test(is_physician)
@require_POST
def call_next_patient_action(request):
    profile = request.staff_profile
    session = current_session(profile)
    if session is None:
        messages.error(request, "Clock in before calling patients.")
        return redirect("doctors_portal:dashboard")
    if QueueEntry.objects.filter(assigned_doctor=profile, status="IN_CONSULTATION").exists():
        messages.error(request, "Complete the current consultation, or send the patient for results, "
                                "before calling the next patient.")
        return redirect("doctors_portal:dashboard")

    next_entry = (QueueEntry.objects.filter(assigned_doctor=profile, status="WAITING")
                  .select_related("encounter__patient").order_by("-priority", "joined_at").first())
    if next_entry is None:
        waiting_pay = QueueEntry.objects.filter(assigned_doctor=profile,
                                                status="AWAITING_PAYMENT").count()
        msg = "No patients are waiting for you."
        if waiting_pay:
            msg += f" {waiting_pay} awaiting payment at the till."
        messages.info(request, msg)
        return redirect("doctors_portal:dashboard")

    patient = next_entry.encounter.patient
    if not can_start_consultation(patient, next_entry.encounter):
        log_action(request, AuditAction.CONSULTATION_BLOCKED_NO_CONSENT,
                   f"Could not call {patient}: no guardian consent on record.")
        messages.error(request,
                       f"{patient.first_name or 'This child'} cannot be seen yet. "
                       "Record guardian consent, or emergency consent with a reason, on the Consent page.")
        return redirect("core_patients:consent", patient_id=patient.id)

    next_entry.status = "IN_CONSULTATION"
    next_entry.save(update_fields=["status", "updated_at"])
    room_label = session.room.name if session.room else "a room"
    notify_on_duty(
        role="RECEPTION", kind="DOCTOR_CALLED_PATIENT",
        message=f"Dr. {profile.user.get_full_name() or profile.user.username} called "
                f"{patient.last_name}, {patient.first_name} to {room_label}.",
        link=reverse("triage_queue:board"),
    )
    log_action(request, AuditAction.QUEUE_NEXT_PATIENT_CALLED,
               f"Called encounter #{next_entry.encounter_id} (patient {patient}).")
    return redirect("doctors_portal:dashboard")


@login_required
@user_passes_test(is_physician)
@require_POST
def request_scan_action(request, encounter_id):
    encounter = get_object_or_404(Encounter, pk=encounter_id)

    try:
        require_active(encounter, request.staff_profile)
    except FlowError as exc:
        messages.error(request, str(exc))
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)

    modality = request.POST.get("imaging_type", "X-Ray")
    body_part = request.POST.get("body_part", "").strip()
    indication = request.POST.get("clinical_indication", "").strip()
    if modality not in SCAN_PRICES:
        messages.error(request, "Choose a valid imaging type.")
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)
    if not body_part or not indication:
        messages.error(request, "Body part and clinical indication are required.")
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)

    with transaction.atomic():
        scan = ScanOrder.objects.create(
            encounter=encounter, ordered_by=request.staff_profile,
            modality=modality, body_part=body_part,
            clinical_indication=indication, status="ORDERED",
        )
        billing_services.add_charge(
            encounter=encounter, source_type="SCAN_ORDER",
            description=f"{modality} ({body_part})",
            unit_price=SCAN_PRICES[modality], staff=request.staff_profile,
            source_id=scan.id, key=f"scan:{scan.id}",
        )
        paused = pause_for_results(encounter, request.staff_profile)

    log_action(request, AuditAction.ENCOUNTER_RECORD_VIEWED,
               f"Ordered {modality} ({body_part}) for {encounter.patient}.")
    if paused:
        messages.success(request, f"{modality} requested. The patient is awaiting results and "
                                  "you are free to take the next patient.")
        return redirect("doctors_portal:dashboard")
    messages.success(request, f"{modality} requested for {body_part}.")
    return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)


@login_required
@user_passes_test(is_physician)
@require_POST
def resume_consultation_action(request, encounter_id):
    encounter = get_object_or_404(Encounter, pk=encounter_id)
    try:
        resume_consultation(encounter, request.staff_profile)
    except FlowError as exc:
        messages.error(request, str(exc))
        return redirect("doctors_portal:dashboard")
    log_action(request, AuditAction.ENCOUNTER_RECORD_VIEWED,
               f"Resumed consultation for {encounter.patient}.")
    return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)


@login_required
@user_passes_test(is_physician)
@require_POST
def cancel_scan_action(request, scan_id):
    """
    Cancels a scan that has not been performed and has no payment against it.
    A scan with any payment on it needs a refund through billing first.
    """
    scan = get_object_or_404(ScanOrder.objects.select_related("encounter__patient"), pk=scan_id)
    if scan.ordered_by_id != request.staff_profile.id:
        messages.error(request, "Only the doctor who ordered this scan can cancel it.")
        return redirect("doctors_portal:consultation_room", encounter_id=scan.encounter_id)
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "Give a reason for cancelling the scan.")
        return redirect("doctors_portal:consultation_room", encounter_id=scan.encounter_id)

    with transaction.atomic():
        scan = ScanOrder.objects.select_for_update().get(pk=scan.pk)
        if not scan_cancellable(scan):
            messages.error(request, "This scan cannot be cancelled. It has been performed, or money has been "
                                    "paid against it. A payment needs a refund through billing.")
            return redirect("doctors_portal:consultation_room", encounter_id=scan.encounter_id)
        charge = scan_charge_or_none(scan)
        if charge is not None:
            billing_services.void_or_reverse(charge, request.staff_profile)
        scan.status = "CANCELLED"
        scan.save(update_fields=["status"])

    log_action(request, AuditAction.ENCOUNTER_RECORD_VIEWED,
               f"Cancelled {scan.modality} ({scan.body_part}) for {scan.encounter.patient}: {reason}")
    messages.success(request, "Scan cancelled. The visit can be completed once nothing else is outstanding.")
    return redirect("doctors_portal:consultation_room", encounter_id=scan.encounter_id)


@login_required
@user_passes_test(is_physician)
def doctor_workstation_view(request, encounter_id):
    encounter = get_object_or_404(Encounter.objects.select_related("patient"), pk=encounter_id)
    return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)


@login_required
@user_passes_test(is_physician)
@require_POST
def save_clinical_note(request, encounter_id):
    return add_clinical_note_action(request, encounter_id)


@login_required
@user_passes_test(is_physician)
@require_POST
def cancel_medication_order_action(request, order_id):
    order = get_object_or_404(MedicationOrder, pk=order_id)
    if order.ordered_by_id != request.staff_profile.id:
        messages.error(request, "Only the prescribing doctor can cancel this prescription.")
        return redirect("doctors_portal:consultation_room", encounter_id=order.encounter_id)
    try:
        pharmacy_services.cancel_medication_order(
            order_id=order.id, staff=request.staff_profile,
            reason=request.POST.get("reason", ""))
    except PharmacyError as exc:
        messages.error(request, str(exc))
    else:
        log_action(request, AuditAction.MEDICATION_ORDERED,
                   f"Cancelled prescription for {order.encounter.patient}: {request.POST.get('reason', '').strip()}")
        messages.success(request, "Prescription cancelled. Any payment on it is now a refund for billing.")
    return redirect("doctors_portal:consultation_room", encounter_id=order.encounter_id)
