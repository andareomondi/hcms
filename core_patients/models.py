import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from simple_history.models import HistoricalRecords


class Department(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True)
    kind = models.CharField(max_length=20, choices=[
        ("CLINICAL", "Clinical"), ("DIAGNOSTIC", "Diagnostic"),
        ("PHARMACY", "Pharmacy"), ("WARD", "Ward"), ("BILLING", "Billing"), ("ADMIN", "Administration"),
    ])

    def __str__(self):
        return self.name


class StaffProfile(models.Model):
    ROLE_CHOICES = [
        ("RECEPTION", "Reception / Registry"),
        ("TRIAGE_NURSE", "Triage Nurse"),
        ("DOCTOR", "Physician"),
        ("NURSE", "Ward / Injection Nurse"),
        ("PHARMACIST", "Pharmacist"),
        ("RADIOLOGIST", "Radiologist"),
        ("BILLING", "Billing Officer"),
        ("ADMIN", "System Administrator"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="staff_profile")
    employee_id = models.CharField(max_length=50, unique=True)
    license_number = models.CharField(max_length=50, unique=True, null=True, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, db_index=True)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="staff")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user.get_full_name()} ({self.get_role_display()})"


class Patient(models.Model):
    ID_TYPES = [
        ("NATIONAL_ID", "National ID"),
        ("BIRTH_CERT", "Birth certificate number"),
        ("PASSPORT", "Passport"),
        ("TEMP_NEWBORN", "Temporary newborn identifier"),
        ("TEMP_EMERGENCY", "Temporary emergency identifier"),
        ("NONE", "No identification presented"),
    ]
    SEX_CHOICES = [("M", "Male"), ("F", "Female"), ("X", "Other"), ("U", "Unknown")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, db_index=True)
    date_of_birth = models.DateField(null=True, blank=True)
    dob_is_estimated = models.BooleanField(default=False)
    sex = models.CharField(max_length=1, choices=SEX_CHOICES, default="U")
    id_type = models.CharField(max_length=20, choices=ID_TYPES, default="NONE")
    id_number = models.CharField(max_length=50, null=True, blank=True)
    is_provisional = models.BooleanField(default=False)
    provisional_label = models.CharField(max_length=120, blank=True)
    merged_into = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="merged_from")
    phone = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="registered_patients")

    history = HistoricalRecords()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["id_type", "id_number"], condition=Q(id_number__isnull=False),
                                    name="uniq_patient_id_per_type"),
        ]

    def __str__(self):
        return f"{self.last_name}, {self.first_name or '(unnamed)'}"

    @property
    def is_minor(self):
        if not self.date_of_birth:
            return None
        today = timezone.localdate()
        age = today.year - self.date_of_birth.year - (
            (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day))
        return age < 18


class PatientRelationship(models.Model):
    RELATIONSHIP_CHOICES = [
        ("PARENT", "Parent"),
        ("GUARDIAN", "Legal guardian"),
        ("SPOUSE", "Spouse"),
        ("SIBLING", "Sibling"),
        ("OTHER", "Other"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="relationships_out")
    related_patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="relationships_in")
    relationship = models.CharField(max_length=20, choices=RELATIONSHIP_CHOICES)
    is_billing_guarantor = models.BooleanField(default=False)
    is_emergency_contact = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["patient", "related_patient", "relationship"],
                                    name="uniq_relationship_triplet"),
            models.CheckConstraint(check=~Q(patient=models.F("related_patient")), name="no_self_relationship"),
            models.UniqueConstraint(fields=["patient"], condition=Q(is_billing_guarantor=True, is_active=True),
                                    name="one_active_guarantor"),
        ]


class AllergenClass(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=150, unique=True)

    def __str__(self):
        return self.name


class Allergen(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    display_name = models.CharField(max_length=200)
    allergen_class = models.ForeignKey(AllergenClass, on_delete=models.PROTECT, related_name="allergens",
                                       null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.display_name


class PatientAllergy(models.Model):
    SEVERITY = [("MILD", "Mild"), ("MODERATE", "Moderate"), ("SEVERE", "Severe / anaphylaxis")]
    STATUS = [("ACTIVE", "Active"), ("RESOLVED", "Resolved"), ("DISPUTED", "Disputed")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="allergies")
    allergen = models.ForeignKey(Allergen, on_delete=models.PROTECT, related_name="patient_allergies")
    severity = models.CharField(max_length=10, choices=SEVERITY)
    status = models.CharField(max_length=10, choices=STATUS, default="ACTIVE", db_index=True)
    recorded_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["patient", "allergen"], condition=Q(status="ACTIVE"),
                                    name="one_active_allergy_per_allergen"),
        ]


class AllergyObservation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient_allergy = models.ForeignKey(PatientAllergy, on_delete=models.PROTECT, related_name="observations")
    reaction = models.TextField()
    observed_at = models.DateTimeField()
    recorded_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)

class StaffRegistrationRequest(models.Model):
    """
    A pending request to create a staff account. No User exists until an
    administrator approves it. The password is stored only as a hash and the
    hash is cleared once the request is reviewed.
    """
    STATUS = [("PENDING", "Pending"), ("APPROVED", "Approved"), ("DECLINED", "Declined")]

    # ADMIN is deliberately absent. Administrator accounts are created by a superuser only.
    REQUESTABLE_ROLES = ["RECEPTION", "TRIAGE_NURSE", "DOCTOR", "NURSE",
                         "PHARMACIST", "RADIOLOGIST", "BILLING"]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=150)
    email = models.EmailField()
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    employee_id = models.CharField(max_length=50)
    requested_role = models.CharField(max_length=20, choices=StaffProfile.ROLE_CHOICES)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="staff_requests")
    password_hash = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="PENDING", db_index=True)
    submitted_at = models.DateTimeField(auto_now_add=True, db_index=True)
    submitted_ip = models.GenericIPAddressField(null=True, blank=True)

    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    decline_reason = models.TextField(blank=True)
    created_user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                        null=True, blank=True, related_name="staff_request")

    class Meta:
        ordering = ["-submitted_at"]
        constraints = [
            # Only one pending request may claim a username or employee ID at a time
            models.UniqueConstraint(fields=["username"], condition=Q(status="PENDING"),
                                    name="one_pending_request_per_username"),
            models.UniqueConstraint(fields=["employee_id"], condition=Q(status="PENDING"),
                                    name="one_pending_request_per_employee_id"),
        ]

    def __str__(self):
        return f"{self.username} ({self.get_requested_role_display()}, {self.status})"

class ConsentRecord(models.Model):
    """
    Append-only. Consent is never edited. A withdrawal is a new record.
    """
    TYPES = [
        ("TREATMENT", "Consent to treatment"),
        ("DATA", "Consent to process health data"),
        ("EMERGENCY", "Emergency treatment without consent"),
        ("WITHDRAWN", "Consent withdrawn"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="consents")
    consent_type = models.CharField(max_length=12, choices=TYPES, db_index=True)
    encounter = models.ForeignKey("triage_queue.Encounter", on_delete=models.PROTECT,
                                  null=True, blank=True, related_name="consents")
    given_by_patient = models.ForeignKey(Patient, on_delete=models.PROTECT, null=True, blank=True,
                                         related_name="+")
    given_by_name = models.CharField(max_length=120, blank=True)
    relationship = models.CharField(max_length=40, blank=True)
    identification = models.CharField(max_length=60, blank=True)
    reason = models.TextField(blank=True)
    recorded_by = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-recorded_at"]

    def __str__(self):
        return f"{self.get_consent_type_display()} for {self.patient}"
