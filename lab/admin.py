from django.contrib import admin

from .models import LabOrder, LabOrderLine, LabResultAmendment, LabTest


@admin.register(LabTest)
class LabTestAdmin(admin.ModelAdmin):
    """
    The catalogue can be edited here. Tests are never deleted, because existing orders refer
    to them. Retire a test by unticking Is active.
    """
    list_display = ("code", "name", "section", "result_type", "unit", "price", "is_active")
    list_filter = ("section", "result_type", "is_active")
    search_fields = ("code", "name", "specimen")
    ordering = ("section", "name")
    readonly_fields = ("id",)

    def has_delete_permission(self, request, obj=None):
        return False


class LabOrderLineInline(admin.TabularInline):
    model = LabOrderLine
    extra = 0
    can_delete = False
    fields = ("test", "price_snapshot", "status", "value_number", "value_text", "flag",
              "entered_by", "entered_at", "credit_approved_by", "credit_reason")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(LabOrder)
class LabOrderAdmin(admin.ModelAdmin):
    """Read-only. Orders change only through the lab workflow, which records each step."""
    list_display = ("id", "encounter", "ordered_by", "status", "second_person_verification",
                    "ordered_at", "released_at")
    list_filter = ("status", "second_person_verification")
    search_fields = ("id", "sample_barcode", "encounter__patient__last_name",
                     "encounter__patient__first_name")
    date_hierarchy = "ordered_at"
    inlines = [LabOrderLineInline]
    readonly_fields = [f.name for f in LabOrder._meta.concrete_fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LabResultAmendment)
class LabResultAmendmentAdmin(admin.ModelAdmin):
    """Read-only audit view. Amendments are created only by the amend action."""
    list_display = ("line", "previous_number", "previous_text", "new_number", "new_text",
                    "amended_by", "amended_at")
    list_filter = ("amended_at",)
    search_fields = ("reason", "line__test__name")
    readonly_fields = [f.name for f in LabResultAmendment._meta.concrete_fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
