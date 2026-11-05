from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin
from .models import (
    AllergyStatusChange, ConsentRecord, Department, PatientMerge, StaffProfile, Patient, PatientRelationship,
    AllergenClass, Allergen, PatientAllergy, AllergyObservation, PatientAllergyStatement, StaffRegistrationRequest
)

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "kind")
    search_fields = ("name", "code")

@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "employee_id", "role", "department", "is_active")
    list_filter = ("role", "is_active", "department")
    search_fields = ("employee_id", "user__username", "user__first_name", "user__last_name")

@admin.register(Patient)
class PatientAdmin(SimpleHistoryAdmin):
    list_display = ("last_name", "first_name", "date_of_birth", "sex", "id_type", "is_provisional", "created_at")
    list_filter = ("is_provisional", "sex", "id_type")
    search_fields = ("last_name", "first_name", "id_number", "provisional_label")

@admin.register(PatientRelationship)
class PatientRelationshipAdmin(SimpleHistoryAdmin):
    list_display = ("patient", "related_patient", "relationship", "is_billing_guarantor", "is_active")
    list_filter = ("relationship", "is_billing_guarantor", "is_active")

@admin.register(AllergenClass)
class AllergenClassAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")

@admin.register(Allergen)
class AllergenAdmin(admin.ModelAdmin):
    list_display = ("code", "display_name", "allergen_class", "is_active")
    list_filter = ("is_active", "allergen_class")
    search_fields = ("code", "display_name")

@admin.register(PatientAllergy)
class PatientAllergyAdmin(SimpleHistoryAdmin):
    list_display = ("patient", "allergen", "severity", "status", "recorded_at")
    list_filter = ("severity", "status")
    search_fields = ("patient__last_name", "allergen__display_name")

@admin.register(AllergyObservation)
class AllergyObservationAdmin(admin.ModelAdmin):
    list_display = ("patient_allergy", "observed_at", "recorded_at")
    search_fields = ("reaction",)

@admin.register(PatientAllergyStatement)
class PatientAllergyStatementAdmin(admin.ModelAdmin):
    list_display = ("patient", "status", "recorded_at", "recorded_by")
    list_filter = ("status",)
    search_fields = ("patient__last_name", "patient__first_name")

@admin.register(StaffRegistrationRequest)
class StaffRegistrationRequestAdmin(admin.ModelAdmin):
    list_display = ("created_user", "username", "status", "requested_role", "first_name", "last_name")
    list_filter = ("status", "requested_role")
    search_fields = ("username", "requested_role", "first_name", "last_name")

@admin.register(ConsentRecord)
class ConsentRecordAdmin(admin.ModelAdmin):
    list_display = ("patient", "consent_type", "given_by_patient",  "given_by_name", "recorded_at", "recorded_by")
    list_filter = ("consent_type", "given_by_patient",  "given_by_name", "recorded_at")
    search_fields = ("patient__last_name", "patient__first_name", "consent_type", "given_by_name", "recorded_by__user__username")

@admin.register(PatientMerge)
class PatientMergeAdmin(admin.ModelAdmin):
    list_display = ("survivor", "duplicate", "merged_by", "merged_at")
    list_filter = ("merged_by", "merged_at")
    search_fields = ("survivor__last_name", "duplicate__last_name", "survivor__first_name", "duplicate__first_name", "merged_by__user__username")

@admin.register(AllergyStatusChange)
class AllergyStatusChangeAdmin(admin.ModelAdmin):
    list_display = ("allergy", "from_status", "to_status", "changed_by", "changed_at")
    list_filter = ("changed_by", "changed_at")
    search_fields = ("changed_by__user__username", "allergy__patient__last_name", "allergy__patient__first_name", "allergy__allergen__display_name")

