import uuid

from django.db import models

from core_patients.models import StaffProfile
from triage_queue.models import Encounter


class ScanOrder(models.Model):
    STATUS = [("ORDERED", "Ordered"), ("PERFORMED", "Performed"),
              ("REPORTED", "Reported"), ("CANCELLED", "Cancelled")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name="scan_orders")
    ordered_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    modality = models.CharField(max_length=60)
    body_part = models.CharField(max_length=100)
    clinical_indication = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS, default="ORDERED", db_index=True)
    ordered_at = models.DateTimeField(auto_now_add=True)

    # Billing credit exception (QOL-2). Set only by a BILLING officer, with a reason.
    credit_approved_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT,
                                           null=True, blank=True, related_name="+")
    credit_approved_at = models.DateTimeField(null=True, blank=True)
    credit_reason = models.TextField(blank=True)


class ImagingStudy(models.Model):
    IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(ScanOrder, on_delete=models.PROTECT, related_name="studies")
    file = models.FileField(upload_to="radiology/%Y/%m/")
    file_sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    report = models.TextField(blank=True)
    reported_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    reported_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_image(self):
        return bool(self.file) and self.file.name.lower().endswith(self.IMAGE_EXTENSIONS)
