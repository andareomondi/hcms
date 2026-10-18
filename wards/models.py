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

    class Meta:
        constraints = [models.UniqueConstraint(fields=["ward", "code"], name="uniq_bed_code_per_ward")]


class Admission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name="admissions")
    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="admissions")
    admitted_at = models.DateTimeField()
    discharged_at = models.DateTimeField(null=True, blank=True)
    admitted_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    planned_days = models.PositiveSmallIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["bed"], condition=Q(discharged_at__isnull=True),
                                    name="one_open_admission_per_bed"),
        ]
