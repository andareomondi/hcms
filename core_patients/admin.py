from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin
from .models import (
    Department, StaffProfile, Patient, PatientRelationship,
    AllergenClass, Allergen, PatientAllergy, AllergyObservation
)

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "kind")
    search_fields = ("name", "code")

@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_user = ("user", "employee_id", "role", "department", "is_active")
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
