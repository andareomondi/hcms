from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin
from .models import BillingAccount, Charge

@admin.register(BillingAccount)
class BillingAccountAdmin(SimpleHistoryAdmin):
    list_display = ("owner", "is_active")
    list_filter = ("is_active",)
    search_fields = ("owner__last_name", "owner__first_name")

@admin.register(Charge)
class ChargeAdmin(admin.ModelAdmin):
    list_display = ("encounter", "treated_patient", "source_type", "amount", "status", "created_at")
    list_filter = ("status", "source_type")
    search_fields = ("description", "treated_patient__last_name", "idempotency_key")
