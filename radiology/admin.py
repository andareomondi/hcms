from django.contrib import admin
from .models import ScanOrder, ImagingStudy


@admin.register(ScanOrder)
class ScanOrderAdmin(admin.ModelAdmin):
    list_display = ("encounter", "modality", "body_part", "status", "ordered_at")
    list_filter = ("status", "modality")
    search_fields = ("modality", "body_part", "encounter__patient__last_name")


@admin.register(ImagingStudy)
class ImagingStudyAdmin(admin.ModelAdmin):
    list_display = ("order", "file_sha256", "uploaded_by", "uploaded_at", "reported_at")
    search_fields = ("file_sha256", "report")# Register your models here.
