import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from billing import services as billing
from core_patients.allergy_services import conflicts_for
from core_patients.models import Patient, PatientRelationship
from hospital_system import validation as v
from pharmacy import services as pharmacy
from pharmacy.models import Medication
from triage_queue.models import Encounter

from .models import (Delivery, DeliveryEpisode, LabourBed, LabourStay, NameCorrection,
                     Pregnancy, Vaccination)
from .prices import DELIVERY_PRICES
from .schedule import LABELS, SCHEDULE_CODES, VACCINE_ROUTE

PREGNANCY_DAYS = 280


class MaternityError(Exception):
    """A rule staff must see. Shown as-is."""


MAX_PREGNANCY_DAYS = 300   # a pregnancy is about 280 days from the last period; allow for dating error
MAX_OVERDUE_DAYS = 60


def _newborn_identifier(delivered_at):
    return f"NB-{timezone.localtime(delivered_at):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def _set_only_guarantor(patient, keep=None):
    """A child has one active billing guarantor. Clear any other."""
    qs = PatientRelationship.objects.filter(patient=patient, is_billing_guarantor=True, is_active=True)
    if keep is not None:
        qs = qs.exclude(pk=keep.pk)
    qs.update(is_billing_guarantor=False)


def _not_male(patient):
    if patient.sex == "M":
        raise MaternityError("Pregnancy and birth can only be recorded for a patient whose sex is not male.")


# ---------- pregnancy ----------

@transaction.atomic
def open_pregnancy(*, patient, lmp, edd, edd_override_reason, staff):
    """
    Opens a pregnancy on the mother's record. The due date is calculated from the LMP
    (LMP + 280 days). A different due date is allowed only with a reason.
    """
    if staff is None:
        raise MaternityError("Your account has no staff profile.")
    if patient.merged_into_id:
        raise MaternityError("This record was merged into another. Open the surviving record.")
    _not_male(patient)
    today = timezone.localdate()
    if lmp is None and edd is None:
        raise MaternityError("Enter the last menstrual period or the due date.")
    try:
        if lmp is not None:
            lmp = v.past_date(lmp, "Last menstrual period")
            if lmp < today - timedelta(days=MAX_PREGNANCY_DAYS):
                raise v.ValidationError("Last menstrual period: more than 300 days ago. "
                                        "Check the date.")
        if edd is not None:
            if edd < today - timedelta(days=MAX_OVERDUE_DAYS):
                raise v.ValidationError("Estimated due date: more than 60 days in the past. Check the date.")
            if edd > today + timedelta(days=MAX_PREGNANCY_DAYS):
                raise v.ValidationError("Estimated due date: more than 300 days away. Check the date.")
    except v.ValidationError as exc:
        raise MaternityError(str(exc))
    if Pregnancy.objects.filter(patient=patient, status="ONGOING").exists():
        raise MaternityError("This patient already has an ongoing pregnancy.")

    calculated = lmp + timedelta(days=PREGNANCY_DAYS) if lmp else None
    reason = (edd_override_reason or "").strip()
    if edd is None:
        due = calculated
    else:
        if calculated and edd != calculated and not reason:
            raise MaternityError("The due date differs from the one calculated from the last period. "
                                 "Give the reason.")
        due = edd

    return Pregnancy.objects.create(
        patient=patient, lmp=lmp, edd=due,
        edd_override_reason=reason if calculated and due != calculated else "",
        opened_by=staff,
    )


@transaction.atomic
def close_pregnancy(*, pregnancy, status, note, staff):
    """
    Ends an ongoing pregnancy without a birth: a pregnancy loss or a transfer out.
    A birth closes the pregnancy itself (see record_delivery).
    """
    if staff is None:
        raise MaternityError("Your account has no staff profile.")
    if status not in ("LOSS", "TRANSFERRED"):
        raise MaternityError("Choose how the pregnancy ended.")
    if not (note or "").strip():
        raise MaternityError("Write a note on how the pregnancy ended.")
    p = Pregnancy.objects.select_for_update().get(pk=pregnancy.pk)
    if p.status != "ONGOING":
        raise MaternityError("This pregnancy is already closed.")
    p.status = status
    p.closed_at = timezone.now()
    p.closing_note = note.strip()
    p.save(update_fields=["status", "closed_at", "closing_note"])
    return p


# ---------- labour and delivery ----------

# Range checks for vitals at birth. Values outside these are refused outright.
VITAL_RANGES = {
    "heart_rate_bpm": ("Heart rate", 40, 250),
    "respiratory_rate": ("Respiratory rate", 10, 120),
    "temperature_c": ("Temperature (°C)", 30, 43),
    "spo2_percent": ("Oxygen saturation (%)", 50, 100),
    "bp_systolic": ("Systolic blood pressure", 30, 200),
    "bp_diastolic": ("Diastolic blood pressure", 15, 150),
    "birth_length_cm": ("Length (cm)", 25, 70),
    "head_circumference_cm": ("Head circumference (cm)", 20, 45),
}


def _vacant_labour_bed():
    """A free labour bed. Created on demand when every existing one is in use."""
    bed = LabourBed.objects.select_for_update().filter(status="VACANT").order_by("code").first()
    if bed is None:
        n = LabourBed.objects.count() + 1
        while LabourBed.objects.filter(code=f"LB-{n}").exists():
            n += 1
        bed = LabourBed.objects.create(code=f"LB-{n}", status="VACANT")
    return bed


@transaction.atomic
def open_labour(*, patient, staff, delivered_before_arrival=False, arrival_note=""):
    """
    A woman arrives in labour (or is brought in after delivering elsewhere).
    Opens a maternity visit, takes a labour bed, and needs NO queue or triage.
    """
    if staff is None:
        raise MaternityError("Your account has no staff profile.")
    if patient.merged_into_id:
        raise MaternityError("This record was merged into another. Open the surviving record.")
    _not_male(patient)
    if LabourStay.objects.filter(patient=patient, discharged_at__isnull=True).exists():
        raise MaternityError("This patient is already in labour. Open her existing labour record.")

    encounter = Encounter.objects.create(
        patient=patient, encounter_type="MATERNITY", payer_type="SELF_PAY", opened_by=staff,
        chief_complaint=("Delivered before arrival" if delivered_before_arrival else "In labour"),
    )
    bed = _vacant_labour_bed()
    bed.status = "OCCUPIED"
    bed.save(update_fields=["status"])
    return LabourStay.objects.create(
        patient=patient, encounter=encounter, labour_bed=bed, arrived_at=timezone.now(),
        delivered_before_arrival=delivered_before_arrival, arrival_note=(arrival_note or "").strip(),
        admitted_by=staff,
    )


def suggest_surname(*, mother, father_last_name=None, father_consents=False):
    """
    Surname to suggest for a newborn. The father's surname wins when his details are given
    and he consents; otherwise the mother's surname is suggested. Staff may still change it.
    """
    if father_last_name and father_consents:
        return father_last_name.strip()
    return mother.last_name


def _check_vitals(baby):
    for field, (label, low, high) in VITAL_RANGES.items():
        value = baby.get(field)
        if value is None or value == "":
            continue
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise MaternityError(f"{label} must be a number.")
        if not (Decimal(low) <= number <= Decimal(high)):
            raise MaternityError(f"{label} must be between {low} and {high}.")


def _check_baby(baby, index):
    tag = f"Baby {index}: " if index else ""
    if baby.get("sex") not in ("M", "F", "X", "U"):
        raise MaternityError(tag + "Choose the baby's sex.")
    for label, value in (("APGAR at 1 minute", baby.get("apgar_1")), ("APGAR at 5 minutes", baby.get("apgar_5"))):
        if value is not None and not (0 <= value <= 10):
            raise MaternityError(tag + f"{label} must be between 0 and 10.")
    weight = baby.get("birth_weight_g")
    if weight is not None and not (200 <= weight <= 8000):
        raise MaternityError(tag + "Birth weight must be between 200 g and 8000 g.")
    _check_vitals(baby)


@transaction.atomic
def record_birth_episode(*, mother_encounter, staff, delivered_at, delivery_type, babies,
                         father=None, complications="", delivered_before_arrival=False):
    """
    Records one childbirth event. `babies` is a list of dicts, one per baby (twins and
    triplets are several entries). Each baby becomes its own patient record, all linked to
    one DeliveryEpisode. The mother is billed one delivery package, whatever the number of babies.

    `father` is optional: {"first_name", "last_name", "consents": bool}. His details are
    recorded only when he consents.
    """
    if mother_encounter.status != "OPEN":
        raise MaternityError("This visit is closed. Record the birth on an open visit.")
    mother = mother_encounter.patient
    _not_male(mother)
    if staff is None:
        raise MaternityError("Your account has no staff profile.")
    if delivery_type not in dict(Delivery.DELIVERY_TYPES):
        raise MaternityError("Choose a delivery type.")
    if delivered_at > timezone.now():
        raise MaternityError("The time of birth cannot be in the future.")
    if not babies:
        raise MaternityError("Record at least one baby.")
    for index, baby in enumerate(babies, start=1 if len(babies) > 1 else 0):
        _check_baby(baby, index)

    father_consents = bool(father and father.get("consents"))
    if father and (father.get("first_name") or father.get("last_name")) and not father_consents:
        raise MaternityError("The father's details can only be recorded with his consent.")

    stay = LabourStay.objects.filter(encounter=mother_encounter).first()
    pregnancy = Pregnancy.objects.select_for_update().filter(patient=mother, status="ONGOING").first()
    local_day = timezone.localtime(delivered_at).date()

    episode = DeliveryEpisode.objects.create(
        mother_encounter=mother_encounter, labour_stay=stay,
        delivered_before_arrival=delivered_before_arrival or bool(stay and stay.delivered_before_arrival),
        complications=(complications or "").strip(), recorded_by=staff,
    )

    father_patient = None
    father_last = (father or {}).get("last_name", "").strip() if father_consents else None
    if father_consents and (father.get("first_name", "").strip() or father_last):
        father_patient = Patient.objects.create(
            first_name=father.get("first_name", "").strip(), last_name=father_last or mother.last_name,
            sex="M", id_type="NONE", created_by=staff,
        )

    for index, data in enumerate(babies):
        first = (data.get("first_name") or "").strip()
        last = (data.get("last_name") or "").strip() or suggest_surname(
            mother=mother, father_last_name=father_last, father_consents=father_consents)
        baby = Patient.objects.create(
            first_name=first, last_name=last, date_of_birth=local_day, dob_is_estimated=False,
            sex=data["sex"], id_type="TEMP_NEWBORN", id_number=_newborn_identifier(delivered_at),
            is_provisional=True, is_temporary_name=(first == ""),
            provisional_label=f"Baby of {mother}, delivered {timezone.localtime(delivered_at):%Y-%m-%d %H:%M}",
            created_by=staff,
        )
        PatientRelationship.objects.create(
            patient=baby, related_patient=mother, relationship="PARENT",
            is_billing_guarantor=True, is_emergency_contact=True,
        )
        if father_patient is not None:
            PatientRelationship.objects.create(
                patient=baby, related_patient=father_patient, relationship="FATHER",
                is_billing_guarantor=False, is_emergency_contact=False,
            )
        Delivery.objects.create(
            mother_encounter=mother_encounter, pregnancy=pregnancy, baby=baby, episode=episode,
            delivered_at=delivered_at, delivery_type=delivery_type, baby_sex=data["sex"],
            birth_weight_g=data.get("birth_weight_g"), apgar_1=data.get("apgar_1"),
            apgar_5=data.get("apgar_5"), gestation_weeks=data.get("gestation_weeks"),
            notes=(data.get("notes") or "").strip(),
            heart_rate_bpm=data.get("heart_rate_bpm"), respiratory_rate=data.get("respiratory_rate"),
            temperature_c=data.get("temperature_c"), spo2_percent=data.get("spo2_percent"),
            bp_systolic=data.get("bp_systolic"), bp_diastolic=data.get("bp_diastolic"),
            birth_length_cm=data.get("birth_length_cm"),
            head_circumference_cm=data.get("head_circumference_cm"),
            recorded_by=staff,
        )
        # The baby's nursery visit. Its charges route to the mother's account via the guarantor link.
        Encounter.objects.create(
            patient=baby, encounter_type="CHILD_HEALTH", payer_type="SELF_PAY", opened_by=staff,
            chief_complaint="Newborn, delivered at "
            f"{timezone.localtime(delivered_at):%H:%M} on {local_day:%Y-%m-%d}",
        )

    if pregnancy is not None:
        pregnancy.status = "DELIVERED"
        pregnancy.closed_at = timezone.now()
        pregnancy.save(update_fields=["status", "closed_at"])

    # One delivery package for the mother, covering the birth and the labour space.
    billing.add_charge(
        encounter=mother_encounter, source_type="MANUAL",
        description=f"Delivery package: {dict(Delivery.DELIVERY_TYPES)[delivery_type]}",
        unit_price=DELIVERY_PRICES[delivery_type], staff=staff,
        source_id=episode.id, key=f"delivery:{episode.id}",
    )
    return episode


def record_delivery(*, mother_encounter, staff, delivered_at, delivery_type, baby_sex,
                    birth_weight_g, apgar_1, apgar_5, gestation_weeks, notes,
                    baby_first_name="", baby_last_name="", **vitals):
    """Single-baby form. Returns the baby's Delivery row. Kept for the existing screen."""
    baby = {
        "sex": baby_sex, "first_name": baby_first_name, "last_name": baby_last_name,
        "birth_weight_g": birth_weight_g, "apgar_1": apgar_1, "apgar_5": apgar_5,
        "gestation_weeks": gestation_weeks, "notes": notes, **vitals,
    }
    episode = record_birth_episode(
        mother_encounter=mother_encounter, staff=staff, delivered_at=delivered_at,
        delivery_type=delivery_type, babies=[baby])
    return episode.babies.get()


@transaction.atomic
def sign_off_episode(*, episode, doctor, note):
    """Only a doctor can sign off a delivery. The note is required."""
    if doctor is None or doctor.role != "DOCTOR":
        raise MaternityError("Only a doctor can sign off a delivery.")
    if episode.status == "SIGNED":
        raise MaternityError("This delivery is already signed off.")
    if not (note or "").strip():
        raise MaternityError("Write the sign-off note before signing off.")
    episode.status = "SIGNED"
    episode.signed_off_by = doctor
    episode.signed_off_at = timezone.now()
    episode.signoff_note = note.strip()
    episode.save(update_fields=["status", "signed_off_by", "signed_off_at", "signoff_note"])
    return episode


# ---------- temporary names ----------

def temporary_children():
    """Babies born here whose name is still temporary."""
    return (Patient.objects.filter(is_temporary_name=True, birth_record__isnull=False)
            .select_related("birth_record__mother_encounter__patient")
            .order_by("birth_record__delivered_at"))


@transaction.atomic
def request_name_correction(*, patient, first_name, last_name, reason, staff):
    if staff is None:
        raise MaternityError("Your account has no staff profile.")
    if not patient.is_temporary_name:
        raise MaternityError("Only a temporary name can be corrected this way.")
    if not (first_name or "").strip() or not (last_name or "").strip():
        raise MaternityError("Enter both the first and last name.")
    if not (reason or "").strip():
        raise MaternityError("Give the reason for the correction.")
    return NameCorrection.objects.create(
        patient=patient, old_first_name=patient.first_name, old_last_name=patient.last_name,
        new_first_name=first_name.strip(), new_last_name=last_name.strip(),
        reason=reason.strip(), requested_by=staff,
    )


@transaction.atomic
def confirm_name_correction(*, correction, confirmer):
    """A second person confirms. Must be a doctor or a system admin, and not the requester."""
    if confirmer is None:
        raise MaternityError("Your account has no staff profile.")
    if correction.status != "PENDING":
        raise MaternityError("This correction has already been decided.")
    if confirmer.pk == correction.requested_by_id:
        raise MaternityError("The person who requested the correction cannot confirm it.")
    if confirmer.role not in ("DOCTOR", "ADMIN"):
        raise MaternityError("Only a doctor or a system admin can confirm a name correction.")

    patient = Patient.objects.select_for_update().get(pk=correction.patient_id)
    patient.first_name = correction.new_first_name
    patient.last_name = correction.new_last_name
    patient.is_temporary_name = False
    patient.save()   # Patient keeps its history trail

    correction.status = "CONFIRMED"
    correction.confirmed_by = confirmer
    correction.confirmed_at = timezone.now()
    correction.save(update_fields=["status", "confirmed_by", "confirmed_at"])
    return correction


# ---------- moving to a ward after birth ----------

@transaction.atomic
def move_to_ward(*, encounter, bed_id, planned_days, reason, staff):
    """
    After birth, the mother or the baby is assigned a ward bed. The bed is billed through
    the normal ward admission. The labour bed is freed when the mother moves.
    """
    from wards import services as wards
    stay = LabourStay.objects.filter(encounter=encounter).first()
    admission = wards.admit(review=encounter, bed_id=bed_id, planned_days=planned_days,
                            reason=reason, staff=staff)
    if stay is not None and not stay.discharged_at:
        stay.discharged_at = timezone.now()
        stay.moved_to_ward = True
        stay.save(update_fields=["discharged_at", "moved_to_ward"])
        stay.labour_bed.status = "CLEANING"
        stay.labour_bed.save(update_fields=["status"])
        encounter.status = "CLOSED"
        encounter.save(update_fields=["status"])
    return admission


# ---------- children ----------

@transaction.atomic
def register_child(*, parent, first_name, last_name, date_of_birth, sex, id_type, id_number,
                   dob_is_estimated, is_guarantor, staff):
    if staff is None:
        raise MaternityError("Your account has no staff profile.")
    if not first_name.strip() or not last_name.strip():
        raise MaternityError("Enter the child's first and last name.")
    if date_of_birth is None:
        raise MaternityError("Enter the child's date of birth, or an estimate.")
    if date_of_birth > timezone.localdate():
        raise MaternityError("The date of birth cannot be in the future.")
    if id_number and Patient.objects.filter(id_type=id_type, id_number=id_number).exists():
        raise MaternityError("A patient with this identifier already exists. Link them instead.")

    child = Patient.objects.create(
        first_name=first_name.strip(), last_name=last_name.strip(),
        date_of_birth=date_of_birth, dob_is_estimated=dob_is_estimated, sex=sex,
        id_type=id_type if id_number else "NONE",
        id_number=id_number or None, created_by=staff,
    )
    if is_guarantor:
        _set_only_guarantor(child)
    PatientRelationship.objects.create(
        patient=child, related_patient=parent, relationship="PARENT",
        is_billing_guarantor=is_guarantor,
    )
    return child


@transaction.atomic
def link_relationship(*, patient, related, relationship, is_guarantor):
    if patient.pk == related.pk:
        raise MaternityError("A person cannot be linked to themselves.")
    if relationship not in dict(PatientRelationship.RELATIONSHIP_CHOICES):
        raise MaternityError("Choose a relationship.")

    link, created = PatientRelationship.objects.get_or_create(
        patient=patient, related_patient=related, relationship=relationship,
        defaults={"is_active": True},
    )
    if not created and not link.is_active:
        link.is_active = True
    if is_guarantor:
        _set_only_guarantor(patient, keep=link)
        link.is_billing_guarantor = True
    link.save()
    return link


def _immunisation_visit_for_vaccine(patient, staff, vaccine):
    return Encounter.objects.create(
        patient=patient, encounter_type="CHILD_HEALTH", payer_type="SELF_PAY",
        opened_by=staff, chief_complaint=f"Immunisation: {LABELS[vaccine]}",
    )


@transaction.atomic
def record_vaccination(*, patient, vaccine, given_at, serial, staff):
    """
    Records one dose. The vial is scanned or picked; the dose goes through pharmacy as a
    nurse-administered order, so stock, the order history and the vaccination record agree.
    The child's allergies are checked first. The visit is recorded and closed at once.
    """
    if staff is None:
        raise MaternityError("Your account has no staff profile.")
    if vaccine not in SCHEDULE_CODES:
        raise MaternityError("Choose a vaccine from the schedule.")
    if patient.date_of_birth is None:
        raise MaternityError("Record the child's date of birth first.")
    if Vaccination.objects.filter(patient=patient, vaccine=vaccine).exists():
        raise MaternityError("This vaccine is already recorded for this child.")
    if not (serial or "").strip():
        raise MaternityError("Scan the vial serial or pick the vial from the list.")

    medication = Medication.objects.filter(vaccine_code=vaccine, is_active=True).first()
    if medication is None:
        raise MaternityError(f"{LABELS[vaccine]} is not set up in Pharmacy yet. Add it there with its vaccine code.")

    hits = list(conflicts_for(patient, medication))
    if hits:
        names = ", ".join(h.allergen.display_name for h in hits)
        raise MaternityError(f"Allergy alert: the child is recorded as allergic to {names}. "
                             "A doctor must review this before the dose is given.")

    visit = _immunisation_visit_for_vaccine(patient, staff, vaccine)
    try:
        order, unit = pharmacy.give_vaccine_dose(
            encounter=visit, medication=medication, serial=serial.strip().upper(),
            nurse=staff, route=VACCINE_ROUTE.get(vaccine, "IM"),
        )
    except pharmacy.PharmacyError as exc:
        raise MaternityError(str(exc))

    vacc = Vaccination.objects.create(
        patient=patient, vaccine=vaccine, given_at=given_at,
        batch_number=unit.batch.batch_number, encounter=visit,
        given_by=staff, stock_unit=unit, order=order,
    )

    charged = False
    if not medication.is_programme_free and medication.unit_price > 0:
        billing.add_charge(
            encounter=visit, source_type="MEDICATION_ORDER",
            description=f"{medication.brand_name}: {LABELS[vaccine]}",
            unit_price=medication.unit_price, staff=staff,
            source_id=order.id, key=f"med:{order.id}",
        )
        charged = True

    visit.status = "CLOSED"
    visit.closed_at = timezone.now()
    visit.save(update_fields=["status", "closed_at"])
    if charged:
        from triage_queue.services import notify_billing_ready
        notify_billing_ready(visit)
    return vacc


@transaction.atomic
def confirm_birth_certificate(*, child, first_name, last_name, certificate_number):
    if not child.is_provisional or not Delivery.objects.filter(baby=child).exists():
        raise MaternityError("Only a provisional newborn record can be confirmed this way.")
    if not (first_name.strip() and last_name.strip() and certificate_number.strip()):
        raise MaternityError("Enter the name and certificate number exactly as on the certificate.")
    cert = certificate_number.strip()
    if Patient.objects.filter(id_type="BIRTH_CERT", id_number=cert).exclude(pk=child.pk).exists():
        raise MaternityError("Another record already uses this certificate number.")
    child.first_name = first_name.strip()
    child.last_name = last_name.strip()
    child.id_type = "BIRTH_CERT"
    child.id_number = cert
    child.is_provisional = False
    child.provisional_label = ""
    child.save()   # Patient keeps its simple-history trail, so the change is recorded
    return child
