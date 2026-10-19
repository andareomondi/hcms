from django.contrib import admin

from .models import Delivery, Vaccination


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("baby", "mother_encounter", "delivered_at", "delivery_type")
    list_filter = ("delivery_type",)


@admin.register(Vaccination)
class VaccinationAdmin(admin.ModelAdmin):
    list_display = ("patient", "vaccine", "given_at", "given_by")
    list_filter = ("vaccine",)
