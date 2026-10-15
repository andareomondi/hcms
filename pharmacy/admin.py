from django.contrib import admin
from .models import Medication, StockItem, StockMovement, MedicationOrder, NursingAdministration, Dispensation


@admin.register(Medication)
class MedicationAdmin(admin.ModelAdmin):
    list_display = ("brand_name", "generic_name", "form", "strength", "unit_price", "is_active")
    list_filter = ("is_active", "form")
    search_fields = ("brand_name", "generic_name")


@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ("medication", "quantity", "reorder_level")
    search_fields = ("medication__brand_name", "medication__generic_name")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("medication", "delta", "reason", "actor", "created_at")
    list_filter = ("reason",)
    search_fields = ("medication__brand_name",)


@admin.register(MedicationOrder)
class MedicationOrderAdmin(admin.ModelAdmin):
    list_display = ("encounter", "medication", "route", "fulfilment", "status", "ordered_at")
    list_filter = ("status", "fulfilment", "route")
    search_fields = ("medication__brand_name", "encounter__patient__last_name")


@admin.register(NursingAdministration)
class NursingAdministrationAdmin(admin.ModelAdmin):
    list_display = ("order", "dose_given_text", "site", "administered_by", "administered_at")
    search_fields = ("order__medication__brand_name", "site")


@admin.register(Dispensation)
class DispensationAdmin(admin.ModelAdmin):
    list_display = ("order", "quantity", "dispensed_by", "dispensed_at")
    search_fields = ("order__medication__brand_name",)# Register your models here.
