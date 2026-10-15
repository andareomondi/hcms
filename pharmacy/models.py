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
               ("ADJUST", "Adjustment"), ("EXPIRE", "Expired / written off")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    medication = models.ForeignKey(Medication, on_delete=models.PROTECT, related_name="movements")
    delta = models.IntegerField()
    reason = models.CharField(max_length=20, choices=REASONS)
    reference_id = models.UUIDField(null=True, blank=True)
    actor = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


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
