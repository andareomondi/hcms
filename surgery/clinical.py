"""
Theatre rules for steps 4 to 9: pre-op checks, consent, payment gate, team, theatre checkpoints,
anaesthesia, recovery, complications, sign-off and theatre stock.
Views call these; the order of checkpoints and every refusal is decided here.
"""
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from billing import services as billing
from billing.models import Charge
from core_patients.models import StaffProfile
from dashboard.audit_actions import AuditAction
from dashboard.utils import create_audit_entry
from hospital_system import validation as v
from triage_queue.intake import validate_triage_vitals

from .models import (
    AnaestheticEvent, PreOpCheck, RecoveryObservation, SurgicalCase, SurgicalComplication,
    SurgicalConsent, SurgicalSignOff, SurgicalTeamMember, TheatreRecord, TheatreStockBatch,
    TheatreStockItem, TheatreStockUse,
)
from .services import SurgeryError

MIN_ELECTIVE_FASTING_HOURS = Decimal("6")
MIN_EMERGENCY_START_REASON = 20
MIN_CHECKPOINT_NOTE = 10
MIN_COMPLICATION_TEXT = 10
GUARDIAN_RELATIONSHIPS = ("PARENT", "LEGAL_GUARDIAN")
CONSENT_STATEMENT = ("I have been told what the operation is, its risks and the alternatives, "
                     "and I agree to it going ahead.")
TEAM_ROLE_STAFF = {
    "SURGEON": "DOCTOR",
    "ANAESTHETIST": "DOCTOR",
    "SCRUB_NURSE": "NURSE",
    "CIRCULATING_NURSE": "NURSE",
    "RECOVERY_NURSE": "NURSE",
}


def _case(case):
    return SurgicalCase.objects.select_for_update().get(pk=case.pk)


def _theatre(case):
    try:
        return TheatreRecord.objects.select_for_update().get(case=case)
    except TheatreRecord.DoesNotExist:
        raise SurgeryError("The theatre has not signed the patient in yet.")


def _require_booked(case):
    if case.status != "BOOKED":
        raise SurgeryError(f"This case is {case.get_status_display().lower()}. No further changes are allowed.")


def _require_on_team(case, staff, roles=None):
    qs = case.team.filter(staff=staff)
    if roles:
        qs = qs.filter(role__in=roles)
    if not qs.exists():
        who = " or ".join(r.lower().replace("_", " ") for r in roles) if roles else "a member of the team"
        raise SurgeryError(f"Only {who} can do this.")


def _age_years(patient):
    dob = patient.date_of_birth
    if not dob:
        return None
    today = timezone.localdate()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


# ---------- step 4: pre-op checks ----------

@transaction.atomic
def record_pre_op(*, case, staff, fasting_hours, vitals, ip=None):
    case = _case(case)
    _require_booked(case)
    if PreOpCheck.objects.filter(case=case).exists():
        raise SurgeryError("The pre-operative check is already recorded.")
    fasting = v.parse_decimal(fasting_hours, "Fasting hours", minimum=0, maximum=72, places=1)
    if case.category == "ELECTIVE" and fasting < MIN_ELECTIVE_FASTING_HOURS:
        raise SurgeryError(f"An elective operation needs at least {MIN_ELECTIVE_FASTING_HOURS} hours of fasting.")
    clean = validate_triage_vitals(vitals)
    if clean.get("pulse_bpm") is None or clean.get("systolic_mmhg") is None:
        raise SurgeryError("Pulse and systolic blood pressure are required before surgery.")
    check = PreOpCheck.objects.create(case=case, fasting_hours=fasting, vitals=clean, recorded_by=staff)
    create_audit_entry(actor=staff.user, action_type=AuditAction.SURGERY_PRE_OP,
                       description=f"Pre-op check recorded for {case.procedure.name}.", ip_address=ip)
    return check


# ---------- step 5: typed consent with guardian rule ----------

def _normal(name):
    return " ".join((name or "").split()).casefold()


@transaction.atomic
def record_consent(*, case, staff, signer_name, relationship, is_guardian, typed_signature, ip=None):
    case = _case(case)
    _require_booked(case)
    if SurgicalConsent.objects.filter(case=case).exists():
        raise SurgeryError("Consent is already recorded for this case. It cannot be changed.")
    signer = v.text(signer_name, "Name of the person signing", max_length=160, required=True)
    typed = v.text(typed_signature, "Typed signature", max_length=160, required=True)
    if relationship not in dict(SurgicalConsent.RELATIONSHIP_CHOICES):
        raise SurgeryError("Choose who is signing.")
    if _normal(signer) != _normal(typed):
        raise SurgeryError("The typed signature must match the name of the person signing.")

    age = _age_years(case.patient)
    if age is None:
        raise SurgeryError("Record the patient's date of birth before taking consent.")
    minor = age < 18
    needs_guardian = minor or case.procedure.requires_guardian_consent
    if needs_guardian:
        if relationship not in GUARDIAN_RELATIONSHIPS or not is_guardian:
            raise SurgeryError("This operation needs consent from a parent or legal guardian, "
                               "who must be marked as the guardian.")
        if minor and relationship == "SELF":
            raise SurgeryError("A minor cannot sign their own consent.")
    else:
        if relationship != "SELF":
            raise SurgeryError("An adult patient signs their own consent.")
        is_guardian = False

    consent = SurgicalConsent.objects.create(
        case=case, signer_name=signer, typed_signature=typed, relationship=relationship,
        is_guardian=bool(is_guardian), statement=CONSENT_STATEMENT, recorded_by=staff)
    create_audit_entry(actor=staff.user, action_type=AuditAction.SURGERY_CONSENT,
                       description=f"Consent for {case.procedure.name} signed by {signer} ({relationship}).",
                       ip_address=ip)
    return consent


# ---------- step 6: payment gate ----------

def outstanding_for_case(case):
    total = Decimal("0")
    for charge in Charge.objects.filter(encounter=case.billing_encounter).exclude(status="VOIDED"):
        total += billing.outstanding_amount(charge)
    return total


# ---------- team ----------

@transaction.atomic
def add_team_member(*, case, staff_profile, role, added_by, ip=None):
    case = _case(case)
    _require_booked(case)
    if role not in TEAM_ROLE_STAFF:
        raise SurgeryError("Choose a valid team role.")
    if staff_profile.role != TEAM_ROLE_STAFF[role]:
        raise SurgeryError(f"This role needs a staff member with the {TEAM_ROLE_STAFF[role].lower()} role.")
    if not staff_profile.is_active:
        raise SurgeryError("This staff member is not active.")
    if case.team.filter(role=role).exists():
        raise SurgeryError("That role is already filled for this case.")
    if case.team.filter(staff=staff_profile).exists():
        raise SurgeryError("This person already has a role in this operation. One person, one role.")
    member = SurgicalTeamMember.objects.create(case=case, role=role, staff=staff_profile, added_by=added_by)
    create_audit_entry(actor=added_by.user, action_type=AuditAction.SURGERY_TEAM,
                       description=f"{staff_profile} added as {role.lower()} for {case.procedure.name}.",
                       ip_address=ip)
    return member


# ---------- step 6 and 7: sign in, checkpoints ----------

@transaction.atomic
def sign_in(*, case, staff, emergency_reason="", ip=None):
    case = _case(case)
    _require_booked(case)
    if TheatreRecord.objects.filter(case=case).exists():
        raise SurgeryError("The patient is already signed in.")
    _require_on_team(case, staff)
    if not PreOpCheck.objects.filter(case=case).exists():
        raise SurgeryError("Record the pre-operative check before sign-in.")
    if not SurgicalConsent.objects.filter(case=case).exists():
        raise SurgeryError("Consent must be recorded before sign-in.")
    team_roles = set(case.team.values_list("role", flat=True))
    if not {"SURGEON", "ANAESTHETIST"} <= team_roles:
        raise SurgeryError("A surgeon and an anaesthetist must be named before sign-in.")

    owed = outstanding_for_case(case)
    reason = (emergency_reason or "").strip()
    if owed > 0:
        if case.category == "ELECTIVE":
            raise SurgeryError(f"Payment of {owed} is outstanding. An elective operation cannot start until it is paid.")
        if len(reason) < MIN_EMERGENCY_START_REASON:
            raise SurgeryError(f"Payment of {owed} is outstanding. An emergency can start only with a reason "
                               f"of at least {MIN_EMERGENCY_START_REASON} characters.")
        create_audit_entry(actor=staff.user, action_type=AuditAction.SURGERY_EMERGENCY_START,
                           description=f"Emergency surgery {case.procedure.name} started with {owed} unpaid: {reason}",
                           ip_address=ip)
    record = TheatreRecord.objects.create(
        case=case, started_by=staff, sign_in_at=timezone.now(),
        emergency_start_reason=reason if owed > 0 else "")
    create_audit_entry(actor=staff.user, action_type=AuditAction.SURGERY_THEATRE,
                       description=f"Patient signed in for {case.procedure.name}.", ip_address=ip)
    return record


def _stamp(case, staff, field, note_field=None, note=None, previous=None, roles=None, ip=None):
    case = _case(case)
    if case.status != "BOOKED":
        raise SurgeryError("This case is no longer open.")
    record = _theatre(case)
    _require_on_team(case, staff, roles)
    if getattr(record, field) is not None:
        raise SurgeryError("This checkpoint is already recorded.")
    if previous and getattr(record, previous) is None:
        raise SurgeryError("Complete the earlier checkpoints first.")
    if note_field is not None:
        text = v.text(note, "Checklist note", max_length=2000, required=True)
        if len(text) < MIN_CHECKPOINT_NOTE:
            raise SurgeryError(f"Write at least {MIN_CHECKPOINT_NOTE} characters for this checkpoint.")
        setattr(record, note_field, text)
    setattr(record, field, timezone.now())
    record.save()
    create_audit_entry(actor=staff.user, action_type=AuditAction.SURGERY_THEATRE,
                       description=f"{field.replace('_at', '').replace('_', ' ').title()} for {case.procedure.name}.",
                       ip_address=ip)
    return record


@transaction.atomic
def time_out(*, case, staff, note, ip=None):
    return _stamp(case, staff, "time_out_at", "time_out_note", note, previous="sign_in_at",
                  roles=("SURGEON", "ANAESTHETIST", "SCRUB_NURSE", "CIRCULATING_NURSE"), ip=ip)


@transaction.atomic
def incision(*, case, staff, ip=None):
    return _stamp(case, staff, "incision_at", previous="time_out_at", roles=("SURGEON",), ip=ip)


@transaction.atomic
def closure(*, case, staff, ip=None):
    return _stamp(case, staff, "closure_at", previous="incision_at", roles=("SURGEON",), ip=ip)


@transaction.atomic
def sign_out(*, case, staff, note, ip=None):
    return _stamp(case, staff, "sign_out_at", "sign_out_note", note, previous="closure_at",
                  roles=("CIRCULATING_NURSE", "SCRUB_NURSE", "SURGEON"), ip=ip)


# ---------- step 7: anaesthesia record ----------

@transaction.atomic
def record_anaesthetic(*, case, staff, given_at, agent, dose, route, ip=None):
    case = _case(case)
    if case.status != "BOOKED":
        raise SurgeryError("This case is no longer open.")
    record = _theatre(case)
    _require_on_team(case, staff, ("ANAESTHETIST",))
    if record.sign_out_at is not None:
        raise SurgeryError("The operation has finished. Anaesthetic lines cannot be added now.")
    if given_at < record.sign_in_at:
        raise SurgeryError("An anaesthetic line cannot be earlier than sign-in.")
    event = AnaestheticEvent.objects.create(
        case=case, given_at=given_at,
        agent=v.text(agent, "Agent", max_length=120, required=True),
        dose=v.text(dose, "Dose", max_length=60, required=True),
        route=v.text(route, "Route", max_length=40, required=True),
        recorded_by=staff)
    create_audit_entry(actor=staff.user, action_type=AuditAction.SURGERY_ANAESTHESIA,
                       description=f"Anaesthetic recorded for {case.procedure.name}: {event.agent}.",
                       ip_address=ip)
    return event


# ---------- step 8: recovery and complications ----------

@transaction.atomic
def record_recovery(*, case, staff, observed_at, vitals, pain_score=None, notes="", ip=None):
    case = _case(case)
    record = _theatre(case)
    _require_on_team(case, staff)
    if record.sign_out_at is None:
        raise SurgeryError("Recovery observations start after sign-out.")
    clean = validate_triage_vitals(vitals)
    pain = v.optional_int(pain_score, "Pain score", minimum=0, maximum=10)
    obs = RecoveryObservation.objects.create(
        case=case, observed_at=observed_at, vitals=clean, pain_score=pain,
        notes=v.text(notes, "Notes", max_length=2000), recorded_by=staff)
    create_audit_entry(actor=staff.user, action_type=AuditAction.SURGERY_RECOVERY,
                       description=f"Recovery observation for {case.procedure.name}.", ip_address=ip)
    return obs


@transaction.atomic
def record_complication(*, case, staff, occurred_at, severity, description, ip=None):
    case = _case(case)
    _theatre(case)
    _require_on_team(case, staff)
    if severity not in dict(SurgicalComplication.SEVERITY_CHOICES):
        raise SurgeryError("Choose the severity.")
    text = v.text(description, "Description of the complication", max_length=2000, required=True)
    if len(text) < MIN_COMPLICATION_TEXT:
        raise SurgeryError(f"Describe the complication in at least {MIN_COMPLICATION_TEXT} characters.")
    item = SurgicalComplication.objects.create(case=case, occurred_at=occurred_at, severity=severity,
                                               description=text, recorded_by=staff)
    create_audit_entry(actor=staff.user, action_type=AuditAction.SURGERY_COMPLICATION,
                       description=f"{item.get_severity_display()} complication for {case.procedure.name}.",
                       ip_address=ip)
    return item


# ---------- step 9: sign-off ----------

def _complete_if_both_signed(case, staff, ip):
    sign = SurgicalSignOff.objects.get(case=case)
    if sign.surgeon_signed_at and sign.anaesthetist_signed_at:
        case.status = "COMPLETED"
        case.save(update_fields=["status"])
        create_audit_entry(actor=staff.user, action_type=AuditAction.SURGERY_COMPLETED,
                           description=f"Surgery completed: {case.procedure.name}.", ip_address=ip)


def _sign(case, staff, role, ip):
    case = _case(case)
    if case.status != "BOOKED":
        raise SurgeryError("This case is no longer open.")
    record = _theatre(case)
    if record.sign_out_at is None:
        raise SurgeryError("Sign-off happens after sign-out.")
    _require_on_team(case, staff, (role,))
    sign, _ = SurgicalSignOff.objects.select_for_update().get_or_create(case=case)
    if role == "SURGEON":
        if sign.surgeon_signed_at:
            raise SurgeryError("The surgeon has already signed.")
        sign.surgeon_signed_by, sign.surgeon_signed_at = staff, timezone.now()
    else:
        if sign.anaesthetist_signed_at:
            raise SurgeryError("The anaesthetist has already signed.")
        sign.anaesthetist_signed_by, sign.anaesthetist_signed_at = staff, timezone.now()
    sign.save()
    create_audit_entry(actor=staff.user, action_type=AuditAction.SURGERY_SIGNED_OFF,
                       description=f"{role.lower()} signed off {case.procedure.name}.", ip_address=ip)
    _complete_if_both_signed(case, staff, ip)
    return sign


@transaction.atomic
def surgeon_sign_off(*, case, staff, ip=None):
    return _sign(case, staff, "SURGEON", ip)


@transaction.atomic
def anaesthetist_sign_off(*, case, staff, ip=None):
    return _sign(case, staff, "ANAESTHETIST", ip)


# ---------- theatre stock ----------

@transaction.atomic
def receive_stock(*, item, batch_number, expiry_date, quantity, staff, ip=None):
    batch_number = v.text(batch_number, "Batch number", max_length=60, required=True)
    qty = v.parse_int(quantity, "Quantity", minimum=1, maximum=100000)
    if not isinstance(expiry_date, date):
        raise SurgeryError("Enter a valid expiry date.")
    if expiry_date < timezone.localdate():
        raise SurgeryError("This stock has already expired and cannot be received.")
    if TheatreStockBatch.objects.filter(item=item, batch_number=batch_number).exists():
        raise SurgeryError("This batch is already recorded for this item.")
    batch = TheatreStockBatch.objects.create(item=item, batch_number=batch_number, expiry_date=expiry_date,
                                             quantity_on_hand=qty, received_by=staff)
    create_audit_entry(actor=staff.user, action_type=AuditAction.SURGERY_STOCK,
                       description=f"Received {qty} {item.unit} of {item.name}, batch {batch_number}.",
                       ip_address=ip)
    return batch


@transaction.atomic
def use_stock(*, case, item, quantity, staff, ip=None):
    """Takes from the batch expiring first. Expired batches are never used."""
    case = _case(case)
    _theatre(case)
    _require_on_team(case, staff)
    qty = v.parse_int(quantity, "Quantity", minimum=1, maximum=100000)
    batches = list(TheatreStockBatch.objects.select_for_update()
                   .filter(item=item, quantity_on_hand__gt=0, expiry_date__gte=timezone.localdate())
                   .order_by("expiry_date", "received_at"))
    available = sum(b.quantity_on_hand for b in batches)
    if available < qty:
        raise SurgeryError(f"Only {available} {item.unit} of {item.name} in stock.")
    remaining, uses = qty, []
    for batch in batches:
        if remaining == 0:
            break
        take = min(batch.quantity_on_hand, remaining)
        batch.quantity_on_hand -= take
        batch.save(update_fields=["quantity_on_hand"])
        uses.append(TheatreStockUse.objects.create(case=case, batch=batch, quantity=take, used_by=staff))
        remaining -= take
    create_audit_entry(actor=staff.user, action_type=AuditAction.SURGERY_STOCK,
                       description=f"Used {qty} {item.unit} of {item.name} for {case.procedure.name}.",
                       ip_address=ip)
    return uses
