import uuid

from django.db import models

from core_patients.models import Patient, StaffProfile


class Encounter(models.Model):
    TYPE_CHOICES = [
        ("OUTPATIENT", "Outpatient"), ("EMERGENCY", "Emergency"),
        ("MATERNITY", "Maternity / delivery"), ("OBSERVATION", "Observation"),
        ("INPATIENT", "In-patient admission"),
    ]
    PAYER_CHOICES = [("SELF_PAY", "Self-pay"), ("INSURANCE", "Insurance (placeholder)")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="encounters")
    encounter_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    payer_type = models.CharField(max_length=20, choices=PAYER_CHOICES, default="SELF_PAY")
    opened_at = models.DateTimeField(auto_now_add=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, default="OPEN",
                              choices=[("OPEN", "Open"), ("CLOSED", "Closed")], db_index=True)
    opened_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    chief_complaint = models.TextField(blank=True, default="")

    # Emergency pre-pay bypass (QOL-2). Set at triage with a reason; cleared by billing.
    prepay_bypassed = models.BooleanField(default=False, db_index=True)
    prepay_bypass_reason = models.TextField(blank=True)
    prepay_bypass_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT,
                                         null=True, blank=True, related_name="+")
    prepay_bypass_at = models.DateTimeField(null=True, blank=True)
    prepay_cleared_at = models.DateTimeField(null=True, blank=True)
    prepay_cleared_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT,
                                          null=True, blank=True, related_name="+")


class TriageVitals(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name="vitals")
    recorded_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)
    temperature_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    pulse_bpm = models.PositiveSmallIntegerField(null=True, blank=True)
    systolic_mmhg = models.PositiveSmallIntegerField(null=True, blank=True)
    diastolic_mmhg = models.PositiveSmallIntegerField(null=True, blank=True)
    respiratory_rate = models.PositiveSmallIntegerField(null=True, blank=True)
    spo2_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)


class QueueEntry(models.Model):
    PRIORITY = [(1, "Routine"), (2, "Urgent"), (3, "Resuscitation")]
    STATUS = [
        ("AWAITING_PAYMENT", "Awaiting payment"),
        ("WAITING", "Waiting"), ("IN_CONSULTATION", "In consultation"),
        ("IN_PROCEDURE", "In procedure / nursing"), ("DIAGNOSTICS", "At radiology / lab"),
        ("ADMITTED", "Admitted to ward"), ("DISCHARGED", "Discharged"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name="queue_entries")
    assigned_doctor = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="queue_entries",
                                        limit_choices_to={"role": "DOCTOR"})
    priority = models.PositiveSmallIntegerField(choices=PRIORITY, default=1)
    status = models.CharField(max_length=20, choices=STATUS, default="WAITING", db_index=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "joined_at"]
