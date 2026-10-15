from django.contrib import admin
from .models import ClinicalNote, ClinicalNoteAddendum


@admin.register(ClinicalNote)
class ClinicalNoteAdmin(admin.ModelAdmin):
    list_display = ("encounter", "author", "created_at")
    search_fields = ("diagnosis", "presenting_complaint", "encounter__patient__last_name")


@admin.register(ClinicalNoteAddendum)
class ClinicalNoteAddendumAdmin(admin.ModelAdmin):
    list_display = ("note", "author", "created_at")
    search_fields = ("text",)# Register your models here.
