import uuid
from django.db import models
from core_patients.models import StaffProfile
from triage_queue.models import Encounter


class ClinicalNote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name="clinical_notes")
    author = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="clinical_notes",
                               limit_choices_to={"role": "DOCTOR"})
    presenting_complaint = models.TextField()
    examination = models.TextField()
    diagnosis = models.TextField()
    plan = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class ClinicalNoteAddendum(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    note = models.ForeignKey(ClinicalNote, on_delete=models.PROTECT, related_name="addenda")
    author = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
