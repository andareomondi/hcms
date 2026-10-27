import uuid

from django.db import models
from django.db.models import Q

from core_patients.models import StaffProfile
from triage_queue.models import Encounter


class LabTest(models.Model):
    SECTIONS = [
        ("HAEMATOLOGY", "Haematology (blood counts)"),
        ("CHEMISTRY", "Chemistry (blood and body fluids)"),
        ("URINALYSIS", "Urinalysis"),
        ("PARASITOLOGY", "Parasitology"),
        ("OTHER", "Other"),
    ]
    RESULT_TYPES = [
        ("NUMERIC", "Number with reference range"),
        ("POSITIVE_NEGATIVE", "Positive / negative"),
        ("TEXT", "Free text"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    section = models.CharField(max_length=20, choices=SECTIONS, db_index=True)
    result_type = models.CharField(max_length=20, choices=RESULT_TYPES, default="NUMERIC")
    unit = models.CharField(max_length=30, blank=True)
    specimen = models.CharField(max_length=80, help_text="e.g. EDTA blood, serum, urine")
    reference_low = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    reference_high = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    reference_text = models.CharField(max_length=120, blank=True,
                                      help_text="For non-numeric tests, e.g. 'Negative'")
    critical_low = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    critical_high = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["section", "name"]
        constraints = [
            models.CheckConstraint(check=Q(price__gte=0), name="lab_test_price_not_negative"),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"


class LabOrder(models.Model):
    STATUS = [
        ("ORDERED", "Ordered"),
        ("RECEIVED", "Sample received"),
        ("RESULTED", "Results entered"),
        ("VERIFIED", "Verified"),
        ("RELEASED", "Released"),
        ("CANCELLED", "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name="lab_orders")
    ordered_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+",
                                   limit_choices_to={"role": "DOCTOR"})
    ordered_at = models.DateTimeField(auto_now_add=True, db_index=True)
    indication = models.TextField()
    status = models.CharField(max_length=12, choices=STATUS, default="ORDERED", db_index=True)
    second_person_verification = models.BooleanField(default=True,
        help_text="Always True for now. False is a later implementation.")
    sample_barcode = models.CharField(max_length=64, unique=True, null=True, blank=True)

    received_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    received_at = models.DateTimeField(null=True, blank=True)

    report = models.TextField(blank=True)
    report_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    report_at = models.DateTimeField(null=True, blank=True)

    verified_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    released_at = models.DateTimeField(null=True, blank=True)

    cancelled_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-ordered_at"]


class LabOrderLine(models.Model):
    STATUS = [
        ("ORDERED", "Ordered"),
        ("RECEIVED", "Received"),
        ("RESULTED", "Resulted"),
        ("RELEASED", "Released"),
        ("CANCELLED", "Cancelled"),
    ]
    FLAGS = [
        ("NORMAL", "Normal"), ("LOW", "Low"), ("HIGH", "High"),
        ("CRITICAL", "Critical"), ("POSITIVE", "Positive"), ("NEGATIVE", "Negative"),
        ("ABNORMAL", "Abnormal"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(LabOrder, on_delete=models.PROTECT, related_name="lines")
    test = models.ForeignKey(LabTest, on_delete=models.PROTECT, related_name="lines")
    price_snapshot = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=12, choices=STATUS, default="ORDERED", db_index=True)

    value_number = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    value_text = models.CharField(max_length=255, blank=True)
    flag = models.CharField(max_length=12, choices=FLAGS, blank=True)
    entered_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    entered_at = models.DateTimeField(null=True, blank=True)

    credit_approved_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    credit_approved_at = models.DateTimeField(null=True, blank=True)
    credit_reason = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order", "test"], name="uniq_test_per_order"),
        ]


class LabResultAmendment(models.Model):
    """Append-only. A released result is never overwritten; each correction is a row here."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    line = models.ForeignKey(LabOrderLine, on_delete=models.PROTECT, related_name="amendments")
    previous_number = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    previous_text = models.CharField(max_length=255, blank=True)
    previous_flag = models.CharField(max_length=12, blank=True)
    new_number = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    new_text = models.CharField(max_length=255, blank=True)
    new_flag = models.CharField(max_length=12, blank=True)
    reason = models.TextField()
    amended_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    amended_at = models.DateTimeField(auto_now_add=True)

