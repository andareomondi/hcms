from django.contrib import admin
from .models import Encounter, TriageVitals, QueueEntry


@admin.register(Encounter)
class EncounterAdmin(admin.ModelAdmin):
    list_display = ("patient", "encounter_type", "payer_type", "status", "opened_at", "closed_at")
    list_filter = ("status", "encounter_type", "payer_type")
    search_fields = ("patient__last_name", "patient__first_name")


@admin.register(TriageVitals)
class TriageVitalsAdmin(admin.ModelAdmin):
    list_display = ("encounter", "temperature_c", "pulse_bpm", "systolic_mmhg", "diastolic_mmhg", "spo2_percent", "recorded_at")
    search_fields = ("encounter__patient__last_name",)


@admin.register(QueueEntry)
class QueueEntryAdmin(admin.ModelAdmin):
    list_display = ("encounter", "assigned_doctor", "priority", "status", "joined_at")
    list_filter = ("priority", "status")
    search_fields = ("encounter__patient__last_name", "assigned_doctor__user__username")# Register your models here.
