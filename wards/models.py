import uuid

from django.db import models
from django.db.models import Q

from core_patients.models import Department, StaffProfile
from triage_queue.models import Encounter


class Ward(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="wards")
    bed_day_rate = models.DecimalField(max_digits=12, decimal_places=2)


class Bed(models.Model):
    STATUS = [("VACANT", "Vacant"), ("OCCUPIED", "Occupied"), ("CLEANING", "Cleaning"),
              ("ISOLATED", "Isolated"), ("OUT_OF_SERVICE", "Out of service")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, related_name="beds")
    code = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=STATUS, default="VACANT", db_index=True)
    status_reason = models.CharField(max_length=255, blank=True)
    status_changed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["ward", "code"], name="uniq_bed_code_per_ward")]


class Admission(models.Model):
    """
    One stay in one bed. The stay is held on its own WARD_STAY encounter, so review
    visits during the stay can close without ending the stay.
    """
    DISCHARGE_STATES = [
        ("ADMITTED", "Admitted"),
        ("DISCHARGE_REQUESTED", "Discharge requested"),
        ("DISCHARGED", "Discharged"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey("core_patients.Patient", on_delete=models.PROTECT,
                                related_name="admissions")
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name="admissions")
    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="admissions")
    admitted_at = models.DateTimeField()
    admitted_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    planned_days = models.PositiveSmallIntegerField(default=1)
    reason = models.TextField(blank=True, default="")

    discharge_state = models.CharField(max_length=20, choices=DISCHARGE_STATES,
                                       default="ADMITTED", db_index=True)
    discharge_requested_at = models.DateTimeField(null=True, blank=True)
    discharge_requested_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT,
                                               null=True, blank=True, related_name="+")
    discharge_confirm_token = models.CharField(max_length=64, blank=True, default="")
    discharge_confirm_expires = models.DateTimeField(null=True, blank=True)
    discharge_approved_at = models.DateTimeField(null=True, blank=True)
    discharge_approved_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT,
                                              null=True, blank=True, related_name="+")
    discharged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["bed"], condition=Q(discharged_at__isnull=True),
                                    name="one_open_admission_per_bed"),
            models.UniqueConstraint(fields=["patient"], condition=Q(discharged_at__isnull=True),
                                    name="one_open_admission_per_patient"),
        ]


class BedStatusLog(models.Model):
    """
    Append-only. Every change to a bed's status is one row. Never edited or deleted.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="status_logs")
    from_status = models.CharField(max_length=20, choices=Bed.STATUS)
    to_status = models.CharField(max_length=20, choices=Bed.STATUS)
    reason = models.CharField(max_length=255, blank=True)
    admission = models.ForeignKey("Admission", on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="bed_status_logs")
    changed_by = models.ForeignKey("core_patients.StaffProfile", on_delete=models.PROTECT, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.bed.code}: {self.from_status} → {self.to_status}"
