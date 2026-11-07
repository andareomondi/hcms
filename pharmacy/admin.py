from django.contrib import admin

from hospital_system.admin_readonly import ReadOnlyAdmin

from .models import (
    Dispensation,
    Medication,
    MedicationOrder,
    NursingAdministration,
    StockBatch,
    StockItem,
    StockMovement,
    StockUnit,
)

# The medicine catalogue stays editable here so prices, forms and vaccine codes can be set up.
# Stock, orders, administrations and dispensing are read-only: they change only through the
# pharmacy and nursing services, so the record of what was received, used and given stays intact.


@admin.register(Medication)
class MedicationAdmin(admin.ModelAdmin):
    list_display = ("brand_name", "generic_name", "form", "strength", "unit_price", "is_active")
    list_filter = ("is_active", "form")
    search_fields = ("brand_name", "generic_name")


@admin.register(StockItem)
class StockItemAdmin(ReadOnlyAdmin):
    list_display = ("medication", "quantity", "reorder_level")
    search_fields = ("medication__brand_name", "medication__generic_name")


@admin.register(StockBatch)
class StockBatchAdmin(ReadOnlyAdmin):
    list_display = ("medication", "batch_number", "expiry_date", "received_by", "received_at")
    list_filter = ("expiry_date",)
    search_fields = ("batch_number", "medication__brand_name")


@admin.register(StockUnit)
class StockUnitAdmin(ReadOnlyAdmin):
    list_display = ("serial", "medication", "batch", "doses_remaining", "doses_total", "status", "opened_at")
    list_filter = ("status",)
    search_fields = ("serial", "medication__brand_name", "batch__batch_number")


@admin.register(StockMovement)
class StockMovementAdmin(ReadOnlyAdmin):
    list_display = ("medication", "delta", "reason", "actor", "created_at")
    list_filter = ("reason",)
    search_fields = ("medication__brand_name",)


@admin.register(MedicationOrder)
class MedicationOrderAdmin(ReadOnlyAdmin):
    list_display = ("encounter", "medication", "route", "fulfilment", "status", "ordered_at")
    list_filter = ("status", "fulfilment", "route")
    search_fields = ("medication__brand_name", "encounter__patient__last_name")


@admin.register(NursingAdministration)
class NursingAdministrationAdmin(ReadOnlyAdmin):
    list_display = ("order", "dose_given_text", "site", "administered_by", "administered_at")
    search_fields = ("order__medication__brand_name", "site")


@admin.register(Dispensation)
class DispensationAdmin(ReadOnlyAdmin):
    list_display = ("order", "quantity", "dispensed_by", "dispensed_at")
    search_fields = ("order__medication__brand_name",)
