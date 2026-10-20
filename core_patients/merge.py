from django.db import transaction
from django.db.models import Q

from billing.models import BillingAccount, Charge, Payment
from dashboard.audit_actions import AuditAction
from dashboard.utils import create_audit_entry
from maternity.models import Delivery, Vaccination
from triage_queue.models import Encounter

from .models import (
    ConsentRecord,
    Patient,
    PatientAllergy,
    PatientMerge,
    PatientRelationship,
)


class MergeError(Exception):
    """A rule staff must see. Shown as-is."""


def _lock_pair(a, b):
    first, second = sorted([a.pk, b.pk])
    return (Patient.objects.select_for_update().get(pk=first),
            Patient.objects.select_for_update().get(pk=second))


def _move_allergies(duplicate, survivor):
    moved = 0
    for allergy in PatientAllergy.objects.filter(patient=duplicate):
        if allergy.status == "ACTIVE" and PatientAllergy.objects.filter(
                patient=survivor, allergen=allergy.allergen, status="ACTIVE").exists():
            allergy.status = "RESOLVED"
            allergy.save(update_fields=["status"])
            continue
        allergy.patient = survivor
        allergy.save(update_fields=["patient"])
        moved += 1
    return moved


def _move_relationships(duplicate, survivor):
    moved = 0
    links = PatientRelationship.objects.filter(
        Q(patient=duplicate) | Q(related_patient=duplicate)).select_related("patient", "related_patient")
    for link in links:
        new_patient = survivor if link.patient_id == duplicate.pk else link.patient
        new_related = survivor if link.related_patient_id == duplicate.pk else link.related_patient

        clash = (new_patient.pk == new_related.pk or PatientRelationship.objects.filter(
            patient=new_patient, related_patient=new_related,
            relationship=link.relationship).exclude(pk=link.pk).exists())
        if clash:
            link.is_active = False
            link.is_billing_guarantor = False
            link.save(update_fields=["is_active", "is_billing_guarantor"])
            continue

        if link.is_billing_guarantor and PatientRelationship.objects.filter(
                patient=new_patient, is_billing_guarantor=True, is_active=True).exclude(pk=link.pk).exists():
            link.is_billing_guarantor = False

        link.patient = new_patient
        link.related_patient = new_related
        link.save()
        moved += 1
    return moved


def _move_billing(duplicate, survivor):
    dup_account = BillingAccount.objects.filter(owner=duplicate).first()
    if dup_account is None:
        return 0
    survivor_account, _ = BillingAccount.objects.get_or_create(owner=survivor)
    moved = Charge.objects.filter(billing_account=dup_account).update(billing_account=survivor_account)
    Payment.objects.filter(billing_account=dup_account).update(billing_account=survivor_account)
    dup_account.is_active = False
    dup_account.save(update_fields=["is_active"])
    return moved


def _fill_gaps(survivor, duplicate):
    changed = []
    if not survivor.phone and duplicate.phone:
        survivor.phone = duplicate.phone
        changed.append("phone")
    if survivor.date_of_birth is None and duplicate.date_of_birth:
        survivor.date_of_birth = duplicate.date_of_birth
        changed.append("date_of_birth")
    if survivor.sex == "U" and duplicate.sex != "U":
        survivor.sex = duplicate.sex
        changed.append("sex")
    if changed:
        survivor.save(update_fields=changed)
    return changed


@transaction.atomic
def merge_patients(*, survivor, duplicate, reason, staff):
    if staff is None:
        raise MergeError("Your account has no staff profile.")
    if survivor.pk == duplicate.pk:
        raise MergeError("Choose two different records.")
    if not reason.strip():
        raise MergeError("Give a reason for the merge.")

    survivor, duplicate = _lock_pair(survivor, duplicate)
    if survivor.merged_into_id or duplicate.merged_into_id:
        raise MergeError("One of these records has already been merged. Open the surviving record and try again.")

    shared_vaccines = (set(Vaccination.objects.filter(patient=duplicate).values_list("vaccine", flat=True))
                       & set(Vaccination.objects.filter(patient=survivor).values_list("vaccine", flat=True)))
    if shared_vaccines:
        raise MergeError("Both records have doses of " + ", ".join(sorted(shared_vaccines))
                         + ". A clinician must review the vaccinations before these records are merged.")
    if (Delivery.objects.filter(baby=duplicate).exists()
            and Delivery.objects.filter(baby=survivor).exists()):
        raise MergeError("Both records are births. They cannot be combined automatically.")

    counts = {
        "encounters": Encounter.objects.filter(patient=duplicate).update(patient=survivor),
        "charges": Charge.objects.filter(treated_patient=duplicate).update(treated_patient=survivor),
        "vaccinations": Vaccination.objects.filter(patient=duplicate).update(patient=survivor),
        "births": Delivery.objects.filter(baby=duplicate).update(baby=survivor),
        "consents": ConsentRecord.objects.filter(patient=duplicate).update(patient=survivor),
    }
    ConsentRecord.objects.filter(given_by_patient=duplicate).update(given_by_patient=survivor)
    counts["allergies"] = _move_allergies(duplicate, survivor)
    counts["relationships"] = _move_relationships(duplicate, survivor)
    counts["billing_charges"] = _move_billing(duplicate, survivor)
    counts["filled_fields"] = _fill_gaps(survivor, duplicate)

    duplicate.merged_into = survivor
    duplicate.save(update_fields=["merged_into"])

    record = PatientMerge.objects.create(
        survivor=survivor, duplicate=duplicate, reason=reason.strip(),
        summary={k: (len(v) if isinstance(v, list) else v) for k, v in counts.items()},
        merged_by=staff,
    )
    create_audit_entry(
        actor=staff.user, action_type=AuditAction.PATIENT_MERGED,
        description=f"Merged {duplicate} into {survivor}: {reason.strip()}",
    )
    return record
