import uuid

from django.db import models
from django.db.models import Q

from core_patients.models import Patient, StaffProfile
from triage_queue.models import Encounter


class Pregnancy(models.Model):
    """
    One pregnancy on the mother's record. Only one can be ongoing at a time.
    Closed by a birth (DELIVERED), a pregnancy loss, or a transfer to another facility.
    """
    STATUS = [
        ("ONGOING", "Ongoing"),
        ("DELIVERED", "Delivered"),
        ("LOSS", "Pregnancy loss"),
        ("TRANSFERRED", "Transferred out"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="pregnancies")
    lmp = models.DateField(null=True, blank=True, verbose_name="Last menstrual period")
    edd = models.DateField(verbose_name="Estimated due date")
    edd_override_reason = models.TextField(blank=True,
                                           help_text="Needed when the due date is not the one calculated from the LMP.")
    status = models.CharField(max_length=12, choices=STATUS, default="ONGOING", db_index=True)
    opened_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    opened_at = models.DateTimeField(auto_now_add=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closing_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(fields=["patient"], condition=Q(status="ONGOING"),
                                    name="one_ongoing_pregnancy"),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.patient_id and self.patient.sex == "M":
            raise ValidationError({"patient": "A pregnancy cannot be recorded for a patient whose sex is male."})

    def __str__(self):
        return f"Pregnancy of {self.patient} (due {self.edd:%Y-%m-%d})"


class Delivery(models.Model):
    """
    Append-only. One row per birth. Corrections are made in the baby's record or by a new note.
    """
    DELIVERY_TYPES = [
        ("SVD", "Normal vaginal delivery"),
        ("ASSISTED", "Assisted delivery"),
        ("CS", "Caesarean section"),
        ("OTHER", "Other"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mother_encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name="deliveries")
    pregnancy = models.ForeignKey(Pregnancy, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="deliveries")
    baby = models.OneToOneField(Patient, on_delete=models.PROTECT, related_name="birth_record")
    delivered_at = models.DateTimeField(db_index=True)
    delivery_type = models.CharField(max_length=10, choices=DELIVERY_TYPES)
    baby_sex = models.CharField(max_length=1, choices=Patient.SEX_CHOICES)
    birth_weight_g = models.PositiveIntegerField(null=True, blank=True)
    apgar_1 = models.PositiveSmallIntegerField(null=True, blank=True)
    apgar_5 = models.PositiveSmallIntegerField(null=True, blank=True)
    gestation_weeks = models.PositiveSmallIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-delivered_at"]
        constraints = [
            models.CheckConstraint(check=Q(apgar_1__isnull=True) | Q(apgar_1__lte=10), name="apgar1_max_10"),
            models.CheckConstraint(check=Q(apgar_5__isnull=True) | Q(apgar_5__lte=10), name="apgar5_max_10"),
        ]

    def __str__(self):
        return f"Birth of {self.baby} on {self.delivered_at:%Y-%m-%d}"


class Vaccination(models.Model):
    """
    Append-only. One dose per vaccine per child. A wrong entry is corrected by an admin, not edited here.
    Every dose is linked to the pharmacy order and the vial it came from.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="vaccinations")
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="vaccinations")
    vaccine = models.CharField(max_length=20, db_index=True)   # code from maternity/schedule.py
    given_at = models.DateTimeField()
    batch_number = models.CharField(max_length=40, blank=True)
    given_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)
    stock_unit = models.ForeignKey("pharmacy.StockUnit", on_delete=models.PROTECT,
                                   null=True, blank=True, related_name="vaccinations")
    order = models.ForeignKey("pharmacy.MedicationOrder", on_delete=models.PROTECT,
                              null=True, blank=True, related_name="vaccinations")

    class Meta:
        ordering = ["given_at"]
        constraints = [
            models.UniqueConstraint(fields=["patient", "vaccine"], name="one_dose_record_per_vaccine"),
        ]

    def __str__(self):
        return f"{self.vaccine} for {self.patient}"
