from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import BillingAccount, Charge, Payment, PaymentAllocation, Refund


# Money records are append-only. Changes go through billing.services and billing.refunds,
# so the admin only reads them. Nothing here can edit or delete a charge, payment or refund.

class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BillingAccount)
class BillingAccountAdmin(SimpleHistoryAdmin):
    list_display = ("owner", "is_active")
    list_filter = ("is_active",)
    search_fields = ("owner__last_name", "owner__first_name")


@admin.register(Charge)
class ChargeAdmin(ReadOnlyAdmin):
    list_display = ("encounter", "treated_patient", "source_type", "amount", "status", "created_at")
    list_filter = ("status", "source_type")
    search_fields = ("description", "treated_patient__last_name", "idempotency_key")
    readonly_fields = ("encounter", "treated_patient", "billing_account", "source_type", "source_id",
                       "idempotency_key", "description", "quantity", "unit_price", "amount",
                       "reversal_of", "status", "created_at", "posted_at", "created_by")


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ("receipt_number", "encounter", "amount", "method", "payer_type", "recorded_by", "recorded_at")
    list_filter = ("method", "payer_type")
    search_fields = ("receipt_number", "reference", "payer_name", "payer_phone",
                     "encounter__patient__last_name")
    readonly_fields = ("receipt_number", "billing_account", "encounter", "amount", "method", "reference",
                       "payer_phone", "recorded_by", "recorded_at", "payer_type", "payer_patient",
                       "payer_name", "payer_relationship", "payer_id_number")


@admin.register(PaymentAllocation)
class PaymentAllocationAdmin(ReadOnlyAdmin):
    list_display = ("payment", "charge", "amount", "created_at")
    search_fields = ("payment__receipt_number", "charge__description")
    readonly_fields = ("payment", "charge", "amount", "created_at")


@admin.register(Refund)
class RefundAdmin(ReadOnlyAdmin):
    list_display = ("encounter", "charge", "amount", "status", "requested_by", "requested_at",
                    "paid_by", "paid_at", "method")
    list_filter = ("status", "method")
    search_fields = ("reason", "reference", "encounter__patient__last_name", "charge__description")
    readonly_fields = ("charge", "encounter", "billing_account", "amount", "reason", "status",
                       "requested_by", "requested_at", "paid_by", "paid_at", "method", "reference")