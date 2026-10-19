import uuid
from datetime import date

from django.db import transaction
from django.utils import timezone

from billing import services as billing
from core_patients.models import Patient, PatientRelationship
from triage_queue.models import Encounter

from .models import Delivery, Vaccination
from .prices import DELIVERY_PRICES
from .schedule import SCHEDULE_CODES


class MaternityError(Exception):
    """A rule staff must see. Shown as-is."""


def _newborn_identifier(delivered_at):
    # A unique temporary identifier. Replace with the hospital's sequence when one is set up (spec 7.3).
    return f"NB-{timezone.localtime(delivered_at):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def _set_only_guarantor(patient, keep=None):
    """A child has one active billing guarantor. Clear any other."""
    qs = PatientRelationship.objects.filter(patient=patient, is_billing_guarantor=True, is_active=True)
    if keep is not None:
        qs = qs.exclude(pk=keep.pk)
    qs.update(is_billing_guarantor=False)


@transaction.atomic
def record_delivery(*, mother_encounter, staff, delivered_at, delivery_type, baby_sex,
                    birth_weight_g, apgar_1, apgar_5, gestation_weeks, notes,
                    baby_first_name="", baby_last_name=""):
    if mother_encounter.status != "OPEN":
        raise MaternityError("This visit is closed. Record the birth on an open visit.")
    if delivery_type not in dict(Delivery.DELIVERY_TYPES):
        raise MaternityError("Choose a delivery type.")
    if baby_sex not in ("M", "F", "X", "U"):
        raise MaternityError("Choose the baby's sex.")
    for label, value in (("APGAR at 1 minute", apgar_1), ("APGAR at 5 minutes", apgar_5)):
        if value is not None and not (0 <= value <= 10):
            raise MaternityError(f"{label} must be between 0 and 10.")
    if birth_weight_g is not None and not (200 <= birth_weight_g <= 8000):
        raise MaternityError("Birth weight must be between 200 g and 8000 g.")
    if staff is None:
        raise MaternityError("Your account has no staff profile.")

    mother = mother_encounter.patient
    local_day = timezone.localtime(delivered_at).date()
    baby = Patient.objects.create(
        first_name=baby_first_name.strip(),
        last_name=baby_last_name.strip() or mother.last_name,
        date_of_birth=local_day,
        sex=baby_sex,
        id_type="TEMP_NEWBORN",
        id_number=_newborn_identifier(delivered_at),
        is_provisional=True,
        provisional_label=f"Baby of {mother}, delivered {timezone.localtime(delivered_at):%Y-%m-%d %H:%M}",
        created_by=staff,
    )
    PatientRelationship.objects.create(
        patient=baby, related_patient=mother, relationship="PARENT",
        is_billing_guarantor=True, is_emergency_contact=True,
    )

    delivery = Delivery.objects.create(
        mother_encounter=mother_encounter, baby=baby, delivered_at=delivered_at,
        delivery_type=delivery_type, baby_sex=baby_sex, birth_weight_g=birth_weight_g,
        apgar_1=apgar_1, apgar_5=apgar_5, gestation_weeks=gestation_weeks,
        notes=notes.strip(), recorded_by=staff,
    )

    # The baby's nursery visit. Its charges route to the mother's account via the guarantor link.
    Encounter.objects.create(
        patient=baby, encounter_type="MATERNITY", payer_type="SELF_PAY",
        opened_by=staff, chief_complaint="Newborn, delivered at "
        f"{timezone.localtime(delivered_at):%H:%M} on {local_day:%Y-%m-%d}",
    )

    billing.add_charge(
        encounter=mother_encounter, source_type="MANUAL",
        description=f"Delivery: {dict(Delivery.DELIVERY_TYPES)[delivery_type]}",
        unit_price=DELIVERY_PRICES[delivery_type], staff=staff,
        source_id=delivery.id, key=f"delivery:{delivery.id}",
    )
    return delivery


@transaction.atomic
def register_child(*, parent, first_name, last_name, date_of_birth, sex, id_type, id_number,
                   dob_is_estimated, is_guarantor, staff):
    if staff is None:
        raise MaternityError("Your account has no staff profile.")
    if not first_name.strip() or not last_name.strip():
        raise MaternityError("Enter the child's first and last name.")
    if date_of_birth is None:
        raise MaternityError("Enter the child's date of birth, or an estimate.")
    if date_of_birth > date.today():
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


@transaction.atomic
def record_vaccination(*, patient, vaccine, given_at, batch_number, encounter, staff):
    if staff is None:
        raise MaternityError("Your account has no staff profile.")
    if vaccine not in SCHEDULE_CODES:
        raise MaternityError("Choose a vaccine from the schedule.")
    if patient.date_of_birth is None:
        raise MaternityError("Record the child's date of birth first.")
    if Vaccination.objects.filter(patient=patient, vaccine=vaccine).exists():
        raise MaternityError("This vaccine is already recorded for this child.")
    return Vaccination.objects.create(
        patient=patient, vaccine=vaccine, given_at=given_at,
        batch_number=batch_number.strip(), encounter=encounter, given_by=staff,
    )


@transaction.atomic
def confirm_birth_certificate(*, child, first_name, last_name, certificate_number):
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
