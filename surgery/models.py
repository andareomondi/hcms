"""
Surgical cases are standalone entries. Each case owns one SURGERY encounter, which is only a billing
container: it is created closed, so it never appears as an open visit or on the triage list.
"""
import uuid

from django.db import models

from django.core.validators import MinValueValidator

from core_patients.models import Patient, StaffProfile
from triage_queue.models import Encounter


class SurgicalProcedure(models.Model):
    CATEGORY_CHOICES = [("MINOR", "Minor"), ("INTERMEDIATE", "Intermediate"), ("MAJOR", "Major")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, unique=True)
    category = models.CharField(max_length=15, choices=CATEGORY_CHOICES)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True, db_index=True)
    requires_guardian_consent = models.BooleanField(
        default=False, help_text="Set for procedures on minors, such as circumcision in childhood.")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SurgicalCase(models.Model):
    CATEGORY_CHOICES = [("ELECTIVE", "Elective"), ("EMERGENCY", "Emergency")]
    STATUS_CHOICES = [("BOOKED", "Booked"), ("CANCELLED", "Cancelled"), ("COMPLETED", "Completed")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="surgical_cases")
    procedure = models.ForeignKey(SurgicalProcedure, on_delete=models.PROTECT, related_name="cases")
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES)
    indication_encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, null=True, blank=True,
                                             related_name="surgical_indications",
                                             help_text="The consultation or admission that decided on the operation.")
    billing_encounter = models.OneToOneField(Encounter, on_delete=models.PROTECT, related_name="surgical_case")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="BOOKED", db_index=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    booked_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    booked_at = models.DateTimeField(auto_now_add=True)
    allergy_clearance_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, null=True, blank=True,
                                             related_name="+")
    allergy_clearance_note = models.TextField(blank=True, default="")
    cancelled_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-booked_at"]


class SurgicalConsent(models.Model):
    """Append-only. One consent per case. The signer types their full name to sign."""
    RELATIONSHIP_CHOICES = [
        ("SELF", "The patient"), ("PARENT", "Parent"), ("LEGAL_GUARDIAN", "Legal guardian"),
        ("OTHER", "Other"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.OneToOneField(SurgicalCase, on_delete=models.PROTECT, related_name="consent")
    signer_name = models.CharField(max_length=160)
    typed_signature = models.CharField(max_length=160)
    relationship = models.CharField(max_length=20, choices=RELATIONSHIP_CHOICES)
    is_guardian = models.BooleanField(default=False)
    statement = models.TextField()
    recorded_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)


class PreOpCheck(models.Model):
    """Fasting, vitals and the consent link. The theatre cannot sign in without this."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.OneToOneField(SurgicalCase, on_delete=models.PROTECT, related_name="pre_op")
    fasting_hours = models.DecimalField(max_digits=4, decimal_places=1, validators=[MinValueValidator(0)])
    vitals = models.JSONField(default=dict, blank=True)
    recorded_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)


class SurgicalTeamMember(models.Model):
    ROLE_CHOICES = [
        ("SURGEON", "Surgeon"),
        ("ANAESTHETIST", "Anaesthetist"),
        ("SCRUB_NURSE", "Scrub nurse"),
        ("CIRCULATING_NURSE", "Circulating nurse"),
        ("RECOVERY_NURSE", "Recovery nurse"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(SurgicalCase, on_delete=models.PROTECT, related_name="team")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    staff = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="surgical_roles")
    added_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["case", "role"], name="surgery_one_person_per_role"),
            models.UniqueConstraint(fields=["case", "staff"], name="surgery_one_role_per_person"),
        ]


class TheatreRecord(models.Model):
    """
    The safety checklist and times. Each checkpoint is stamped once, in order:
    sign-in, time-out, incision, closure, sign-out.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.OneToOneField(SurgicalCase, on_delete=models.PROTECT, related_name="theatre")
    emergency_start_reason = models.TextField(blank=True, default="")
    started_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    sign_in_at = models.DateTimeField()
    time_out_at = models.DateTimeField(null=True, blank=True)
    time_out_note = models.TextField(blank=True, default="")
    incision_at = models.DateTimeField(null=True, blank=True)
    closure_at = models.DateTimeField(null=True, blank=True)
    sign_out_at = models.DateTimeField(null=True, blank=True)
    sign_out_note = models.TextField(blank=True, default="")


class AnaestheticEvent(models.Model):
    """One line of the anaesthesia record: an agent given, at a time, by the anaesthetist."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(SurgicalCase, on_delete=models.PROTECT, related_name="anaesthetic_events")
    given_at = models.DateTimeField()
    agent = models.CharField(max_length=120)
    dose = models.CharField(max_length=60)
    route = models.CharField(max_length=40)
    recorded_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)


class RecoveryObservation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(SurgicalCase, on_delete=models.PROTECT, related_name="recovery_observations")
    observed_at = models.DateTimeField()
    vitals = models.JSONField(default=dict, blank=True)
    pain_score = models.PositiveSmallIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    recorded_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)


class SurgicalComplication(models.Model):
    SEVERITY_CHOICES = [
        ("MINOR", "Minor, no lasting effect"),
        ("MODERATE", "Moderate, needed extra treatment"),
        ("SEVERE", "Severe, prolonged stay or lasting harm"),
        ("LIFE_THREATENING", "Life-threatening"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(SurgicalCase, on_delete=models.PROTECT, related_name="complications")
    occurred_at = models.DateTimeField()
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES)
    description = models.TextField()
    recorded_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)


class SurgicalSignOff(models.Model):
    """The surgeon and the anaesthetist each sign for their own part. Both are needed to complete the case."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.OneToOneField(SurgicalCase, on_delete=models.PROTECT, related_name="sign_off")
    surgeon_signed_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, null=True, blank=True,
                                          related_name="+")
    surgeon_signed_at = models.DateTimeField(null=True, blank=True)
    anaesthetist_signed_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, null=True, blank=True,
                                               related_name="+")
    anaesthetist_signed_at = models.DateTimeField(null=True, blank=True)


class TheatreStockItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, unique=True)
    unit = models.CharField(max_length=30, default="each")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class TheatreStockBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(TheatreStockItem, on_delete=models.PROTECT, related_name="batches")
    batch_number = models.CharField(max_length=60)
    expiry_date = models.DateField()
    quantity_on_hand = models.PositiveIntegerField(default=0)
    received_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["expiry_date"]
        constraints = [
            models.UniqueConstraint(fields=["item", "batch_number"], name="theatre_batch_unique"),
        ]


class TheatreStockUse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(SurgicalCase, on_delete=models.PROTECT, related_name="stock_used")
    batch = models.ForeignKey(TheatreStockBatch, on_delete=models.PROTECT, related_name="uses")
    quantity = models.PositiveIntegerField()
    used_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    used_at = models.DateTimeField(auto_now_add=True)
