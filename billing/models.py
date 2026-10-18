import uuid

from django.db import models
from django.db.models import Q
from simple_history.models import HistoricalRecords

from core_patients.models import Patient, StaffProfile
from triage_queue.models import Encounter


class BillingAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.OneToOneField(Patient, on_delete=models.PROTECT, related_name="billing_account")
    is_active = models.BooleanField(default=True)

    history = HistoricalRecords()

class Payment(models.Model):
    """
    Append-only. A payment is never edited or deleted. A mistake is corrected by a
    new record. Every row keeps who took it and when.
    """
    METHOD = [("MANUAL", "Manual (cash or other)"), ("MPESA", "M-Pesa")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    receipt_number = models.CharField(max_length=24, unique=True)
    billing_account = models.ForeignKey(BillingAccount, on_delete=models.PROTECT, related_name="payments")
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=10, choices=METHOD, db_index=True)
    reference = models.CharField(max_length=60, blank=True)   # M-Pesa transaction code, or manual note
    payer_phone = models.CharField(max_length=20, blank=True)
    recorded_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="payments_taken")
    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-recorded_at"]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="payment_amount_positive"),
            # The same M-Pesa code cannot be recorded twice
            models.UniqueConstraint(fields=["reference"], condition=Q(method="MPESA"),
                                    name="uniq_mpesa_reference"),
        ]

    def __str__(self):
        return f"{self.receipt_number} KES {self.amount} ({self.get_method_display()})"

class Charge(models.Model):
    STATUS = [("PROVISIONAL", "Provisional hold"), ("POSTED", "Posted"),
              ("PAID", "Paid in full"), ("VOIDED", "Voided")]
    SOURCES = [("MEDICATION_ORDER", "Medication order"), ("BED_DAY", "Bed day"),
               ("SCAN_ORDER", "Scan order"), ("CONSULTATION", "Consultation"), ("MANUAL", "Manual adjustment")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name="charges")
    treated_patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="+")
    billing_account = models.ForeignKey(BillingAccount, on_delete=models.PROTECT, related_name="charges")
    source_type = models.CharField(max_length=30, choices=SOURCES)
    source_id = models.UUIDField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=120, unique=True, null=True, blank=True)
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reversal_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversals")
    status = models.CharField(max_length=15, choices=STATUS, default="PROVISIONAL", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
