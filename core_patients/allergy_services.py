from django.db import transaction
from django.utils import timezone

from .models import Allergen, AllergyObservation, PatientAllergy


class AllergyError(Exception):
    """A rule staff must see. Shown as-is."""


def conflicts_for(patient, medication):
    """
    Active allergies whose allergen class matches one of the medicine's classes.
    Matching by class means spelling variants of a brand cannot get past the check.
    """
    class_ids = medication.allergen_classes.values_list("id", flat=True)
    return (PatientAllergy.objects
            .filter(patient=patient, status="ACTIVE", allergen__allergen_class_id__in=class_ids)
            .select_related("allergen"))


@transaction.atomic
def record_allergy(*, patient, allergen_code, severity, reaction, staff):
    if staff is None:
        raise AllergyError("Your account has no staff profile.")
    allergen = Allergen.objects.filter(code=allergen_code, is_active=True).first()
    if allergen is None:
        raise AllergyError("Choose an allergen from the catalogue.")
    if severity not in dict(PatientAllergy.SEVERITY):
        raise AllergyError("Choose a severity.")

    allergy, created = PatientAllergy.objects.get_or_create(
        patient=patient, allergen=allergen, status="ACTIVE",
        defaults={"severity": severity, "recorded_by": staff},
    )
    if not created and allergy.severity != severity:
        allergy.severity = severity
        allergy.save(update_fields=["severity"])

    if reaction.strip():
        AllergyObservation.objects.create(
            patient_allergy=allergy, reaction=reaction.strip(),
            observed_at=timezone.now(), recorded_by=staff,
        )
    return allergy, created


@transaction.atomic
def change_allergy_status(*, patient, allergy_id, status, reason, staff):
    if staff is None:
        raise AllergyError("Your account has no staff profile.")
    if status not in ("RESOLVED", "DISPUTED"):
        raise AllergyError("Choose resolved or disputed.")
    reason = reason.strip()
    if not reason:
        raise AllergyError("Give a reason for changing this allergy.")

    allergy = (PatientAllergy.objects.select_for_update()
               .select_related("allergen").filter(pk=allergy_id, patient=patient).first())
    if allergy is None:
        raise AllergyError("Allergy not found for this patient.")
    if allergy.status != "ACTIVE":
        raise AllergyError("This allergy is not active.")

    allergy.status = status
    allergy.save(update_fields=["status"])
    AllergyObservation.objects.create(
        patient_allergy=allergy,
        reaction=f"Marked {status.lower()}: {reason}",
        observed_at=timezone.now(), recorded_by=staff,
    )
    return allergy
