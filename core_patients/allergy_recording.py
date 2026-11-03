from django.db import transaction
from django.utils import timezone

from dashboard.audit_actions import AuditAction  # noqa: F401  (views log)

from .models import (
    Allergen,
    AllergyStatusChange,
    PatientAllergy,
    PatientAllergyStatement,
)

# Allowed status moves. Resolved is final: a confirmed allergy again is recorded as a new entry.
ALLOWED_MOVES = {
    ("ACTIVE", "DISPUTED"),
    ("ACTIVE", "RESOLVED"),
    ("DISPUTED", "ACTIVE"),
    ("DISPUTED", "RESOLVED"),
}
REASON_REQUIRED = {"DISPUTED", "RESOLVED"}


class AllergyError(Exception):
    """Shown to staff as-is."""


def set_statement(*, patient, status, staff):
    if status not in dict(PatientAllergyStatement.STATUS):
        raise AllergyError("Choose a valid allergy status.")
    stmt, _ = PatientAllergyStatement.objects.update_or_create(
        patient=patient, defaults={"status": status, "recorded_by": staff},
    )
    return stmt


@transaction.atomic
def add_allergen(*, display_name, allergen_class, staff):
    """Staff add an allergen missing from the catalogue. It is flagged for review."""
    name = (display_name or "").strip()
    if not name:
        raise AllergyError("Enter the name of the allergen.")
    if allergen_class is None:
        raise AllergyError("Choose the allergen class. Medicine checks use the class.")
    existing = Allergen.objects.filter(display_name__iexact=name, is_active=True).first()
    if existing:
        raise AllergyError(f"'{existing.display_name}' is already in the catalogue. Select it instead.")
    code = "STAFF-" + "".join(ch for ch in name.upper() if ch.isalnum())[:30]
    code = f"{code}-{timezone.now():%H%M%S}"
    return Allergen.objects.create(
        code=code[:40], display_name=name[:120], allergen_class=allergen_class,
        added_by=staff, needs_review=True,
    )


@transaction.atomic
def record_allergy(*, patient, allergen, severity, reaction, source, staff):
    """Adds an allergy entry. Refused if an active entry already exists for this allergen."""
    if severity not in dict(PatientAllergy.SEVERITY):
        raise AllergyError("Choose the severity: mild, moderate or severe.")
    if source not in dict(PatientAllergy.SOURCE):
        raise AllergyError("Choose where this allergy came from.")
    if PatientAllergy.objects.filter(patient=patient, allergen=allergen, status="ACTIVE").exists():
        raise AllergyError(f"{allergen.display_name} is already recorded as an active allergy.")
    entry = PatientAllergy.objects.create(
        patient=patient, allergen=allergen, severity=severity,
        reaction=(reaction or "").strip()[:300], source=source, recorded_by=staff,
    )
    set_statement(patient=patient, status="RECORDED", staff=staff)
    return entry


@transaction.atomic
def change_allergy_status(*, allergy, to_status, reason, staff):
    """
    Moves an allergy between Active, Disputed and Resolved. Every move is written to history.
    Reason is required for Disputed and Resolved.
    """
    entry = PatientAllergy.objects.select_for_update().get(pk=allergy.pk)
    reason = (reason or "").strip()
    if (entry.status, to_status) not in ALLOWED_MOVES:
        raise AllergyError(f"This allergy cannot move from {entry.get_status_display()} "
                           f"to {to_status.title()}.")
    if to_status in REASON_REQUIRED and not reason:
        raise AllergyError("Give a reason for this change.")
    AllergyStatusChange.objects.create(
        allergy=entry, from_status=entry.status, to_status=to_status,
        reason=reason[:300] or "Reinstated", changed_by=staff,
    )
    entry.status = to_status
    entry.status_reason = reason[:300]
    entry.save(update_fields=["status", "status_reason", "updated_at"])
    return entry


def registration_allergy_status(*, entries, statement_choice):
    """
    Decides the patient's allergy status at registration.
    entries: list of recorded allergies (may be empty).
    statement_choice: "NONE_KNOWN", "UNKNOWN" or "" from the form.
    Rules: any entry means RECORDED. Otherwise the explicit choice is used. If nothing is
    chosen, the status falls back to UNKNOWN.
    """
    if entries:
        return "RECORDED"
    if statement_choice == "NONE_KNOWN":
        return "NONE_KNOWN"
    return "UNKNOWN"
