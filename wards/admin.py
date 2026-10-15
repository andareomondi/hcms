from django.contrib import admin
from .models import Ward, Bed, Admission


@admin.register(Ward)
class WardAdmin(admin.ModelAdmin):
    list_display = ("name", "department", "bed_day_rate")
    search_fields = ("name",)


@admin.register(Bed)
class BedAdmin(admin.ModelAdmin):
    list_display = ("ward", "code", "status")
    list_filter = ("status", "ward")
    search_fields = ("code", "ward__name")


@admin.register(Admission)
class AdmissionAdmin(admin.ModelAdmin):
    list_display = ("encounter", "bed", "admitted_at", "discharged_at", "admitted_by")
    list_filter = ("bed__ward",)
    search_fields = ("encounter__patient__last_name", "bed__code")# Register your models here.
