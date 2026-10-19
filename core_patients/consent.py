from django.db import transaction

from .models import ConsentRecord, PatientRelationship


class ConsentError(Exception):
    """A rule staff must see. Shown as-is."""


def needs_consent(patient):
    """Minors and patients with an unknown date of birth need a guardian's consent."""
    return patient.is_minor is not False


def treatment_consent_status(patient):
    """
    ADULT      - no guardian consent needed
    CONSENTED  - a current guardian consent is on record
    MISSING    - no consent recorded yet
    WITHDRAWN  - consent was withdrawn and has not been given again
    """
    if not needs_consent(patient):
        return "ADULT"
    latest = (patient.consents
              .filter(consent_type__in=["TREATMENT", "WITHDRAWN"])
              .first())
    if latest is None:
        return "MISSING"
    return "CONSENTED" if latest.consent_type == "TREATMENT" else "WITHDRAWN"


def can_start_consultation(patient, encounter):
    """True if the patient may be called in for this visit."""
    if treatment_consent_status(patient) in ("ADULT", "CONSENTED"):
        return True
    return patient.consents.filter(consent_type="EMERGENCY", encounter=encounter).exists()


@transaction.atomic
def record_consent(*, patient, consent_type, staff, given_by_name="", given_by_patient=None,
                   relationship="", identification="", reason="", encounter=None):
    if staff is None:
        raise ConsentError("Your account has no staff profile.")
    if consent_type not in dict(ConsentRecord.TYPES):
        raise ConsentError("Choose the type of consent.")

    if consent_type in ("TREATMENT", "DATA"):
        if given_by_patient is None and not given_by_name.strip():
            raise ConsentError("Name the person giving consent.")
        if given_by_patient is not None and not PatientRelationship.objects.filter(
                patient=patient, related_patient=given_by_patient, is_active=True).exists():
            raise ConsentError("The person giving consent must be linked to this patient first.")
        if consent_type == "TREATMENT" and not relationship.strip() and given_by_patient is None:
            raise ConsentError("Say how the person giving consent is related to the patient.")

    if consent_type == "EMERGENCY":
        if encounter is None or encounter.patient_id != patient.id or encounter.status != "OPEN":
            raise ConsentError("Emergency consent must be recorded on this patient's open visit.")
        if not reason.strip():
            raise ConsentError("Document why guardian consent could not be obtained.")

    if consent_type == "WITHDRAWN":
        if not reason.strip():
            raise ConsentError("Give the reason consent is withdrawn.")

    if given_by_patient is not None and not given_by_name.strip():
        given_by_name = f"{given_by_patient.first_name} {given_by_patient.last_name}".strip()

    return ConsentRecord.objects.create(
        patient=patient,
        consent_type=consent_type,
        encounter=encounter,
        given_by_patient=given_by_patient,
        given_by_name=given_by_name.strip(),
        relationship=relationship.strip(),
        identification=identification.strip(),
        reason=reason.strip(),
        recorded_by=staff,
    )
