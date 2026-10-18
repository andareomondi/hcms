from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing.models import BillingAccount, Charge
from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from duty.services import current_session
from notifications.services import notify_on_duty
from pharmacy.models import Medication, MedicationOrder
from radiology.models import ScanOrder
from triage_queue.models import Encounter, QueueEntry
from wards.models import Admission, Bed, Ward

from .models import ClinicalNote


def is_physician(user):
    return hasattr(user, "staff_profile") and user.staff_profile.role in ("DOCTOR", "ADMIN")


@login_required
@user_passes_test(is_physician)
def consultation_room_view(request, encounter_id):
    """Comprehensive doctor consultation room handling notes, prescriptions, ward admissions, and encounter closure."""
    encounter = get_object_or_404(
        Encounter.objects.select_related("patient", "opened_by"),
        pk=encounter_id
    )
    patient = encounter.patient
    
    # Fetch existing clinical notes, medication orders, and active admission for this encounter
    clinical_notes = encounter.clinical_notes.select_related("author").all().order_by("-created_at")
    medication_orders = encounter.medication_orders.select_related("medication", "ordered_by").all().order_by("-ordered_at")
    active_admission = Admission.objects.filter(encounter=encounter, discharged_at__isnull=True).select_related("bed__ward").first()
    
    # Formulary & Wards for forms
    medications = Medication.objects.all().order_by("brand_name")
    wards = Ward.objects.prefetch_related("beds").all()
    
    context = {
        "encounter": encounter,
        "patient": patient,
        "clinical_notes": clinical_notes,
        "medication_orders": medication_orders,
        "active_admission": active_admission,
        "medications": medications,
        "wards": wards,
    }
    return render(request, "doctors_portal/consultation_room.html", context)


@login_required
@user_passes_test(is_physician)
@require_POST
def add_clinical_note_action(request, encounter_id):
    """Saves clinical examination notes and diagnosis."""
    encounter = get_object_or_404(Encounter, pk=encounter_id)
    
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
            author=getattr(request.user, "staff_profile", None)
        )
        log_action(request, AuditAction.CLINICAL_NOTE_SAVED, f"Added clinical note & diagnosis for patient {encounter.patient}.")
        messages.success(request, "Clinical notes saved successfully.")
    else:
        messages.error(request, "Presenting complaint and diagnosis are required.")
        
    return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)

@login_required
@user_passes_test(is_physician)
@require_POST
def prescribe_medication_action(request, encounter_id):
    """Prescribes a medication and creates a corresponding billing charge."""
    encounter = get_object_or_404(Encounter, pk=encounter_id)
    medication_id = request.POST.get("medication_id")
    dose_input = request.POST.get("dose_text", "").strip()
    route = request.POST.get("route", "ORAL")
    frequency = request.POST.get("frequency", "BID")
    duration = request.POST.get("duration", "5 days")

    medication = get_object_or_404(Medication, pk=medication_id)

    combined_dosage = f"{dose_input or medication.strength} | {route} | {frequency} for {duration}"

    order = MedicationOrder.objects.create(
        encounter=encounter,
        medication=medication,
        dose_text=combined_dosage,
        ordered_by=getattr(request.user, "staff_profile", None),
        status="ORDERED",
    )

    billing_account, _ = BillingAccount.objects.get_or_create(owner=encounter.patient)
    charge_amount = (
        float(medication.unit_price)
        if hasattr(medication, "unit_price") and medication.unit_price
        else 250.00
    )

    Charge.objects.create(
        encounter=encounter,
        treated_patient=encounter.patient,
        billing_account=billing_account,
        source_type="MEDICATION_ORDER",
        source_id=order.id,
        description=f"Prescription: {medication.brand_name} ({combined_dosage})",
        quantity=1,
        unit_price=charge_amount,
        amount=charge_amount,
        status="PROVISIONAL",
        created_by=request.user.staff_profile,
    )

    log_action(
        request,
        AuditAction.MEDICATION_ORDERED,
        f"Prescribed {medication.brand_name} for patient {encounter.patient}.",
    )
    messages.success(request, f"Successfully prescribed {medication.brand_name}.")
    return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)


@login_required
@user_passes_test(is_physician)
@require_POST
def admit_patient_action(request, encounter_id):
    """Admits the patient to a ward and bed from the consultation room."""
    encounter = get_object_or_404(Encounter, pk=encounter_id)
    bed_id = request.POST.get("bed_id")
    
    bed = get_object_or_404(Bed, pk=bed_id)
    if bed.status != "VACANT":
        messages.error(request, f"Bed {bed.code} is currently occupied.")
        return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)
        
    Admission.objects.create(
        encounter=encounter,
        bed=bed,
        admitted_at=timezone.now(),
        admitted_by=getattr(request.user, "staff_profile", None)
    )
    
    bed.status = "OCCUPIED"
    bed.save(update_fields=["status"])
    
    log_action(request, AuditAction.BED_ASSIGNED, f"Admitted patient {encounter.patient} to Ward {bed.ward.name}, Bed {bed.code}.")
    messages.success(request, f"Patient successfully admitted to Bed {bed.code} ({bed.ward.name}).")
    return redirect("doctors_portal:consultation_room", encounter_id=encounter.id)


@login_required
@user_passes_test(is_physician)
@require_POST
def complete_consultation_action(request, encounter_id):
    """Marks consultation as complete, clearing the patient from the doctor's queue."""
    encounter = get_object_or_404(Encounter, pk=encounter_id)
    
    QueueEntry.objects.filter(encounter=encounter, status="IN_CONSULTATION").update(status="COMPLETED")
    encounter.status = "CLOSED"
    encounter.save(update_fields=["status"])
    
    log_action(request, AuditAction.CONSULTATION_COMPLETED, f"Completed clinical consultation for encounter #{encounter.id} (patient {encounter.patient}).")
    messages.success(request, f"Consultation for {encounter.patient} completed successfully.")
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
    """Calls the highest priority waiting patient into consultation."""
    doctor_profile = request.staff_profile
    session = current_session(doctor_profile)
    if session is None:
        messages.error(request, "Clock in before calling patients.")
        return redirect("doctors_portal:dashboard")

    current = QueueEntry.objects.filter(
        assigned_doctor=doctor_profile, status="IN_CONSULTATION"
    ).select_related("encounter__patient").first()

    if current:
        current.status = "DISCHARGED"
        current.save(update_fields=["status"])
        current.encounter.status = "CLOSED"
        current.encounter.save(update_fields=["status"])
        log_action(request, AuditAction.CONSULTATION_COMPLETED,
                   f"Completed consultation for encounter #{current.encounter_id} "
                   f"(patient {current.encounter.patient}).")

    next_entry = QueueEntry.objects.filter(
        assigned_doctor=doctor_profile, status="WAITING"
    ).select_related("encounter__patient").order_by("-priority", "joined_at").first()

    if next_entry:
        next_entry.status = "IN_CONSULTATION"
        next_entry.save(update_fields=["status"])
        patient = next_entry.encounter.patient
        room_label = session.room.name if session.room else "a room"
        notify_on_duty(
            role="RECEPTION",
            kind="DOCTOR_CALLED_PATIENT",
            message=f"Dr. {doctor_profile.user.get_full_name() or doctor_profile.user.username} "
                    f"called {patient.last_name}, {patient.first_name} to {room_label}.",
            link=reverse("triage_queue:board"),
        )
        log_action(request, AuditAction.QUEUE_NEXT_PATIENT_CALLED,
                   f"Called encounter #{next_entry.encounter_id} "
                   f"(patient {patient}, priority {next_entry.priority}).")
    else:
        messages.info(request, "No patients are waiting for you.")

    return redirect("doctors_portal:dashboard")
@login_required
@user_passes_test(is_physician)
@require_POST
def request_scan_action(request, encounter_id):
    """Allows a physician to order a radiology scan from the consultation room."""
    encounter = get_object_or_404(Encounter, pk=encounter_id)
    modality = request.POST.get("modality", "X-Ray")
    body_part = request.POST.get("body_part", "").strip()
    clinical_indication = request.POST.get("clinical_indication", "").strip()
    
    if body_part and clinical_indication:
        scan_order = ScanOrder.objects.create(
            encounter=encounter,
            ordered_by=request.user.staff_profile,
            modality=modality,
            body_part=body_part,
            clinical_indication=clinical_indication,
            status="ORDERED"
        )
        
        log_action(request, AuditAction.ENCOUNTER_RECORD_VIEWED, f"Ordered {modality} ({body_part}) for patient {encounter.patient}.")
        messages.success(request, f"Successfully ordered {modality} scan for {body_part}.")
    else:
        messages.error(request, "Body part and clinical indication are required.")
        
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
