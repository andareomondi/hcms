import uuid

from django.db import models
from django.db.models import Q

from core_patients.models import StaffProfile
from triage_queue.models import Encounter


class Medication(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brand_name = models.CharField(max_length=255, db_index=True)
    generic_name = models.CharField(max_length=255, db_index=True)
    form = models.CharField(max_length=50)
    strength = models.CharField(max_length=50)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)
    allergen_classes = models.ManyToManyField("core_patients.AllergenClass", blank=True,
                                              related_name="medications")
    vaccine_code = models.CharField(max_length=20, blank=True, null=True, unique=True,
                                    help_text="Set only for vaccines. Must match a code in maternity/schedule.py.")
    doses_per_unit = models.PositiveSmallIntegerField(default=1,
                                                      help_text="Doses in one pack or vial. 10 for a 10-dose vial.")
    in_use_hours = models.PositiveSmallIntegerField(null=True, blank=True,
                                                    help_text="Hours an opened multi-dose vial can be used.")
    is_programme_free = models.BooleanField(default=False,
                                            help_text="Free to the patient under the national programme.")
    requires_serial_scan = models.BooleanField(default=False,
                                               help_text="Every dose must be taken from a scanned serial.")

    def clean(self):
        from django.core.exceptions import ValidationError

        from maternity.schedule import SCHEDULE_CODES
        if self.vaccine_code and self.vaccine_code not in SCHEDULE_CODES:
            raise ValidationError({"vaccine_code": "This code is not on the immunisation schedule."})
        if self.vaccine_code:
            self.requires_serial_scan = True

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["brand_name", "generic_name", "form", "strength"],
                                    name="uniq_medication_variant"),
        ]

    def __str__(self):
        return f"{self.brand_name} {self.strength} {self.form}"


class StockItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    medication = models.OneToOneField(Medication, on_delete=models.PROTECT, related_name="stock")
    quantity = models.IntegerField(default=0)
    reorder_level = models.IntegerField(default=10)

    class Meta:
        constraints = [models.CheckConstraint(check=Q(quantity__gte=0), name="stock_not_negative")]


class StockMovement(models.Model):
    REASONS = [("RECEIPT", "Receipt"), ("DISPENSE", "Dispensed"), ("ADMINISTER", "Administered"),
               ("ADJUST", "Adjustment"), ("EXPIRE", "Expired"), ("WRITE_OFF", "Written off")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    medication = models.ForeignKey(Medication, on_delete=models.PROTECT, related_name="movements")
    delta = models.IntegerField()
    reason = models.CharField(max_length=20, choices=REASONS)
    reference_id = models.UUIDField(null=True, blank=True)
    actor = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    unit = models.ForeignKey("StockUnit", on_delete=models.PROTECT, null=True, blank=True,
                             related_name="movements")


class MedicationOrder(models.Model):
    ROUTES = [("ORAL", "Oral"), ("IM", "Intramuscular"), ("IV", "Intravenous"),
              ("SC", "Subcutaneous"), ("TOPICAL", "Topical"), ("OTHER", "Other")]
    FULFILMENT = [("PHARMACY_DISPENSE", "Pharmacy dispense"), ("NURSE_ADMINISTER", "Nurse administration")]
    STATUS = [("ORDERED", "Ordered"), ("PARTIALLY_DONE", "Partially done"),
              ("COMPLETED", "Completed"), ("CANCELLED", "Cancelled")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name="medication_orders")
    ordered_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+",
                                   limit_choices_to={"role": "DOCTOR"})
    medication = models.ForeignKey(Medication, on_delete=models.PROTECT, related_name="orders")
    route = models.CharField(max_length=10, choices=ROUTES)
    fulfilment = models.CharField(max_length=20, choices=FULFILMENT)
    dose_text = models.CharField(max_length=255)
    frequency_text = models.CharField(max_length=100, blank=True)
    duration_days = models.PositiveSmallIntegerField(null=True, blank=True)
    doses_total = models.PositiveSmallIntegerField(default=1)
    instructions = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="ORDERED", db_index=True)
    allergy_override_reason = models.TextField(blank=True)
    ordered_at = models.DateTimeField(auto_now_add=True)
    cancelled_reason = models.TextField(blank=True)


class NursingAdministration(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(MedicationOrder, on_delete=models.PROTECT, related_name="administrations")
    administered_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    administered_at = models.DateTimeField(auto_now_add=True, db_index=True)
    dose_given_text = models.CharField(max_length=255)
    site = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)


class Dispensation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(MedicationOrder, on_delete=models.PROTECT, related_name="dispensations")
    quantity = models.PositiveIntegerField()
    dispensed_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+",
                                     limit_choices_to={"role": "PHARMACIST"})
    dispensed_at = models.DateTimeField(auto_now_add=True, db_index=True)


class StockBatch(models.Model):
    """One delivery of one medicine, with its batch number and expiry."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    medication = models.ForeignKey(Medication, on_delete=models.PROTECT, related_name="batches")
    batch_number = models.CharField(max_length=40)
    expiry_date = models.DateField(db_index=True)
    received_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["expiry_date"]
        constraints = [
            models.UniqueConstraint(fields=["medication", "batch_number"], name="uniq_batch_per_medication"),
        ]

    def __str__(self):
        return f"{self.medication.brand_name} batch {self.batch_number}"


class StockUnit(models.Model):
    """One physical vial or pack, identified by its serial. Doses are taken from it."""
    STATUS = [("IN_STOCK", "In stock"), ("EMPTY", "Used up"),
              ("EXPIRED", "Expired"), ("WRITTEN_OFF", "Written off")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    serial = models.CharField(max_length=64, unique=True)
    medication = models.ForeignKey(Medication, on_delete=models.PROTECT, related_name="units")
    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name="units")
    doses_total = models.PositiveSmallIntegerField()
    doses_remaining = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=12, choices=STATUS, default="IN_STOCK", db_index=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["batch__expiry_date", "received_at"]
        constraints = [
            models.CheckConstraint(check=models.Q(doses_remaining__gte=0), name="unit_doses_not_negative"),
            models.CheckConstraint(check=models.Q(doses_remaining__lte=models.F("doses_total")),
                                   name="unit_doses_within_total"),
        ]

    def __str__(self):
        return f"{self.serial} ({self.medication.brand_name}, {self.doses_remaining}/{self.doses_total} doses)"
