from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
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
from notifications.services import notify_on_duty
from pharmacy.models import Medication, MedicationOrder
from radiology.models import ScanOrder
from triage_queue.models import Encounter, QueueEntry
from triage_queue.services import (
    FlowError,
    close_consultation,
    consultation_mode,
    require_active,
)
from wards import services as ward_services
from wards.models import Admission, Ward

from .models import ClinicalNote


def is_physician(user):
    return hasattr(user, "staff_profile") and user.staff_profile.role in ("DOCTOR", "ADMIN")


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
    medications = Medication.objects.all().order_by("brand_name")
    wards = Ward.objects.prefetch_related("beds").all()

    latest_vitals = encounter.vitals.order_by("-recorded_at").first()
    vitals_history = encounter.vitals.order_by("-recorded_at")[:10]
    scan_orders = list(
        encounter.scan_orders
        .prefetch_related("studies")
        .order_by("-ordered_at")
    )
    for scan in scan_orders:
        studies = list(scan.studies.all())   # uses the prefetched cache
        scan.latest_study = max(studies, key=lambda s: s.uploaded_at) if studies else None
    allergies = (PatientAllergy.objects
                 .filter(patient=patient, status="ACTIVE")
                 .select_related("allergen"))
    tally = billing_services.encounter_tally(encounter)
    mode, queue_entry = consultation_mode(encounter, request.staff_profile)

    context = {
        "encounter": encounter,
        "patient": patient,
        "clinical_notes": clinical_notes,
        "medication_orders": medication_orders,
        "active_admission": active_admission,
        "medications": medications,
        "wards": wards,
        "latest_vitals": latest_vitals,
        "vitals_history": vitals_history,
        "scan_orders": scan_orders,
        "allergies": allergies,
        "tally": tally,
        "scan_prices": SCAN_PRICES,
        "mode": mode,
        "queue_entry": queue_entry,
        "can_act": mode in ("ACTIVE", "IN_WARD"),
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
    medication = get_object_or_404(Medication, pk=request.POST.get("medication_id"), is_active=True)
    route = request.POST.get("route", "ORAL")
    frequency = request.POST.get("frequency", "OD")
    dose_text = request.POST.get("dose_text", "").strip() or medication.strength
    override = request.POST.get("allergy_override_reason", "").strip()
    try:
        days = max(1, int(request.POST.get("duration_days", "5")))
    except ValueError:
        days = 5

    hits = list(conflicts_for(encounter.patient, medication))
    if hits and not override:
        names = ", ".join(h.allergen.display_name for h in hits)
        messages.error(request,
                       f"Allergy alert: the patient is recorded as allergic to {names}. "
                       "Enter an override reason to prescribe anyway.")
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)

    per_day = {"OD": 1, "BID": 2, "TID": 3, "QID": 4, "STAT": 1}.get(frequency, 1)
    doses_total = per_day * days if frequency != "STAT" else 1
    fulfilment = "PHARMACY_DISPENSE" if route == "ORAL" else "NURSE_ADMINISTER"

    with transaction.atomic():
        order = MedicationOrder.objects.create(
            encounter=encounter, ordered_by=profile, medication=medication,
            route=route, fulfilment=fulfilment, dose_text=dose_text,
            frequency_text=frequency, duration_days=days, doses_total=doses_total,
            status="ORDERED", allergy_override_reason=override if hits else "",
        )
        billing_services.add_charge(
            encounter=encounter, source_type="MEDICATION_ORDER",
            description=f"{medication.brand_name} {medication.strength} x{doses_total}",
            unit_price=medication.unit_price, quantity=doses_total, staff=profile,
            source_id=order.id, key=f"med:{order.id}",
        )

    if hits:
        names = ", ".join(h.allergen.display_name for h in hits)
        log_action(request, AuditAction.ALLERGY_OVERRIDDEN,
                   f"Prescribed {medication.brand_name} despite recorded allergy to {names} "
                   f"for {encounter.patient}. Reason: {override}")
    log_action(request, AuditAction.MEDICATION_ORDERED,
               f"Prescribed {medication.brand_name} ({fulfilment}) for {encounter.patient}.")
    messages.success(request, f"Prescribed {medication.brand_name}.")
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

    context = {
        "session": session,
        "current_consultation": current_consultation,
        "waiting_entries": waiting_entries,
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
        messages.error(request, "Complete the current consultation before calling the next patient.")
        return redirect("doctors_portal:dashboard")

    next_entry = (QueueEntry.objects.filter(assigned_doctor=profile, status="WAITING")
                  .select_related("encounter__patient").order_by("-priority", "joined_at").first())
    if next_entry is None:
        messages.info(request, "No patients are waiting for you.")
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
    log_action(request, AuditAction.ENCOUNTER_RECORD_VIEWED,
               f"Ordered {modality} ({body_part}) for {encounter.patient}.")
    messages.success(request, f"{modality} requested for {body_part}.")
    return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)


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
