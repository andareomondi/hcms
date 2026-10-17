from django.db import IntegrityError, transaction
from django.utils import timezone

from core_patients.models import StaffProfile
from pharmacy.models import Medication  # noqa: F401  (keeps app loading order explicit)
from triage_queue.models import QueueEntry

from .models import DutySession


class DutyError(Exception):
    """Raised with a message that can be shown to the user."""


def on_duty_staff(role=None):
    """Active staff who are currently clocked in. Optionally filtered by role."""
    qs = StaffProfile.objects.filter(
        is_active=True,
        duty_sessions__clocked_out_at__isnull=True,
    ).select_related("user", "department")
    if role:
        qs = qs.filter(role=role)
    return qs.distinct()


def current_session(staff):
    if staff is None:
        return None
    return (DutySession.objects.select_related("room")
            .filter(staff=staff, clocked_out_at__isnull=True).first())


def available_rooms(department=None):
    """Active rooms with nobody clocked into them right now."""
    from .models import Room
    occupied = DutySession.objects.filter(clocked_out_at__isnull=True, room__isnull=False) \
        .values_list("room_id", flat=True)
    qs = Room.objects.filter(is_active=True).exclude(id__in=occupied)
    if department is not None:
        qs = qs.filter(department=department)
    return qs


def clock_in(staff, room):
    if staff.role == "DOCTOR" and room is None:
        raise DutyError("Doctors must choose a room to clock in.")
    try:
        with transaction.atomic():
            session = DutySession.objects.create(staff=staff, room=room)
    except IntegrityError:
        # Either already clocked in, or the room was just taken by someone else
        if current_session(staff):
            raise DutyError("You are already clocked in.")
        raise DutyError("That room was just taken. Choose another room.")
    return session


def clock_out(staff):
    session = current_session(staff)
    if session is None:
        raise DutyError("You are not clocked in.")

    if staff.role == "DOCTOR":
        open_patients = QueueEntry.objects.filter(
            assigned_doctor=staff,
            status__in=["WAITING", "IN_CONSULTATION"],
        ).count()
        if open_patients:
            raise DutyError(
                f"You still have {open_patients} patient(s) assigned. "
                "Ask reception to reassign them before clocking out."
            )

    session.clocked_out_at = timezone.now()
    session.save(update_fields=["clocked_out_at"])
    return session


def change_room(staff, room):
    session = current_session(staff)
    if session is None:
        raise DutyError("You must be clocked in to change rooms.")
    try:
        with transaction.atomic():
            session.room = room
            session.save(update_fields=["room"])
    except IntegrityError:
        raise DutyError("That room is occupied.")
    return session
