import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone

from core_patients.models import Department, StaffProfile


class Room(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="rooms")
    code = models.CharField(max_length=20, unique=True)        # e.g. "CLINIC-3"
    name = models.CharField(max_length=100)                    # e.g. "Consulting Room 3"
    floor = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} · {self.name}"


class DutySession(models.Model):
    """
    One row per clock-in. clocked_out_at is NULL while the staff member is on duty.
    The partial unique constraints guarantee one open session per person and one
    occupant per room, enforced by the database even if application code has a bug.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    staff = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="duty_sessions")
    room = models.ForeignKey(Room, on_delete=models.PROTECT, null=True, blank=True, related_name="duty_sessions")
    clocked_in_at = models.DateTimeField(default=timezone.now, db_index=True)
    clocked_out_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-clocked_in_at"]
        constraints = [
            models.UniqueConstraint(fields=["staff"], condition=Q(clocked_out_at__isnull=True),
                                    name="one_open_duty_session_per_staff"),
            models.UniqueConstraint(fields=["room"],
                                    condition=Q(clocked_out_at__isnull=True, room__isnull=False),
                                    name="one_open_duty_session_per_room"),
        ]

    def __str__(self):
        state = "on duty" if self.clocked_out_at is None else "off duty"
        return f"{self.staff} ({state})"
