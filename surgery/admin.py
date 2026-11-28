from django.contrib import admin

from hospital_system.admin_readonly import ReadOnlyAdmin

from .models import (
    AnaestheticEvent, PreOpCheck, RecoveryObservation, SurgicalCase, SurgicalComplication, SurgicalConsent,
    SurgicalProcedure, SurgicalSignOff, SurgicalTeamMember, TheatreRecord, TheatreStockBatch, TheatreStockItem,
    TheatreStockUse,
)

# The procedure catalogue and the stock item list stay editable. Every clinical record is read-only here:
# it changes only through the theatre services, so the record of what was done stays intact.


@admin.register(SurgicalProcedure)
class SurgicalProcedureAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "requires_guardian_consent", "is_active")
    list_filter = ("is_active", "category")
    search_fields = ("name",)


@admin.register(TheatreStockItem)
class TheatreStockItemAdmin(admin.ModelAdmin):
    list_display = ("name", "unit", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(SurgicalCase)
class SurgicalCaseAdmin(ReadOnlyAdmin):
    list_display = ("patient", "procedure", "category", "status", "booked_at")
    list_filter = ("status", "category")
    search_fields = ("patient__last_name", "patient__first_name", "procedure__name")


@admin.register(SurgicalConsent, PreOpCheck, SurgicalTeamMember, TheatreRecord, AnaestheticEvent,
                RecoveryObservation, SurgicalComplication, SurgicalSignOff, TheatreStockBatch, TheatreStockUse)
class SurgeryRecordAdmin(ReadOnlyAdmin):
    list_per_page = 50
