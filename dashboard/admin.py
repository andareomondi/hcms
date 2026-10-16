from django.contrib import admin
from .models import AuditLog

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "actor", "action_type", "description", "ip_address")
    search_fields = ("description", "action_type", "actor__username", "ip_address")
    list_filter = ("action_type", "timestamp")
    ordering = ("-timestamp",)
