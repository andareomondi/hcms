from django.contrib import admin

from .models import DutySession, Room


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "department", "floor", "is_active")
    list_filter = ("department", "is_active")
    search_fields = ("code", "name")


@admin.register(DutySession)
class DutySessionAdmin(admin.ModelAdmin):
    list_display = ("staff", "room", "clocked_in_at", "clocked_out_at")
    list_filter = ("room",)
