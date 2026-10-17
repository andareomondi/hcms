import uuid

from django.db import models

from core_patients.models import StaffProfile


class Notification(models.Model):
    """
    One row per recipient. Broadcasts to a role fan out into one row per on-duty
    staff member, so each person has their own read state.
    """
    KIND = [
        ("DOCTOR_CALLED_PATIENT", "Doctor called a patient"),
        ("PATIENT_ASSIGNED", "Patient assigned to you"),
        ("SYSTEM", "System"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=40, choices=KIND, db_index=True)
    message = models.TextField()
    link = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "read_at"], name="notif_recipient_unread_idx")]

    def __str__(self):
        return f"{self.get_kind_display()} → {self.recipient}"
