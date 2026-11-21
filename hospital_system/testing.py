"""
Shared helpers for the test suite. Builds the minimum real records a test needs
(staff by role, patients, visits) so each app's tests stay short and readable.
"""
from django.contrib.auth import get_user_model
from django.utils import timezone

from core_patients.models import Department, Patient, StaffProfile

_counter = {"n": 0}


def _next():
    _counter["n"] += 1
    return _counter["n"]


_DEPT_KIND = {
    "BILLING": ("BILLING", "Billing", "BILLING"),
    "ADMIN": ("ADMIN", "Administration", "ADMIN"),
    "PHARMACIST": ("PHARMACY", "Pharmacy", "PHARMACY"),
    "RADIOLOGIST": ("DIAGNOSTIC", "Radiology", "DIAGNOSTIC"),
    "LAB_TECHNICIAN": ("DIAGNOSTIC", "Laboratory", "DIAGNOSTIC"),
    "LAB_SUPERVISOR": ("DIAGNOSTIC", "Laboratory", "DIAGNOSTIC"),
    "NURSE": ("WARD", "Wards", "WARD"),
}


def make_staff(role="RECEPTION", username=None, **extra):
    """A staff member with a real user account and a profile for the given role."""
    n = _next()
    code, name, kind = _DEPT_KIND.get(role, ("CLINICAL", "Clinical", "CLINICAL"))
    dept, _ = Department.objects.get_or_create(code=code, defaults={"name": name, "kind": kind})
    user = get_user_model().objects.create_user(
        username=username or f"user{n}", password="x-test-password-123")
    return StaffProfile.objects.create(
        user=user, employee_id=f"EMP{n:04d}", role=role, department=dept, is_active=True, **extra)


def make_patient(created_by, last_name=None, first_name="Test", date_of_birth=None, **extra):
    n = _next()
    return Patient.objects.create(
        first_name=first_name, last_name=last_name or f"Patient{n}",
        date_of_birth=date_of_birth, created_by=created_by, **extra)


def make_encounter(patient, opened_by, encounter_type="OUTPATIENT", **extra):
    from triage_queue.models import Encounter
    return Encounter.objects.create(
        patient=patient, encounter_type=encounter_type, opened_by=opened_by, **extra)


def years_ago(years):
    return timezone.localdate().replace(year=timezone.localdate().year - years)
