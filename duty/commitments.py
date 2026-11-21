"""
Open commitments: work a doctor still owns and must hand over before they stop duty.

A doctor cannot clock out, or log out, while any of these exist:
  - a patient assigned to them that has not been discharged, or
  - a ward stay they admitted that has not been discharged.

This is the single source of truth. Clock-out, logout and triage all call it,
so the rule cannot drift between them.
"""
from triage_queue.models import QueueEntry


def open_commitments(staff):
    """Human-readable list of what still blocks this person from stopping duty. Empty if none."""
    if staff is None or staff.role != "DOCTOR":
        return []

    blockers = []

    assigned = QueueEntry.objects.filter(assigned_doctor=staff).exclude(status="DISCHARGED").count()
    if assigned:
        blockers.append(f"{assigned} patient(s) still assigned to you")

    from wards.models import Admission  # local import: avoids a circular import at startup
    admitted = Admission.objects.filter(admitted_by=staff, discharged_at__isnull=True).count()
    if admitted:
        blockers.append(f"{admitted} ward patient(s) you admitted and have not discharged")

    return blockers


def require_on_duty_doctor(profile):
    """Raise DutyError unless this is an active doctor who is currently clocked in."""
    from duty.services import DutyError, current_session

    if profile is None or profile.role != "DOCTOR" or not profile.is_active:
        raise DutyError("Choose a doctor who is on duty.")
    if current_session(profile) is None:
        raise DutyError("Choose a doctor who is on duty.")
