from django.urls import path
from . import views

app_name = "wards"

urlpatterns = [
    path("", views.ward_dashboard_view, name="dashboard"),
    path("bed-grid/", views.ward_dashboard_view, name="bed_grid"), # alias for existing nav links
    path("bed/<uuid:bed_id>/assign/", views.assign_bed_action, name="assign_bed"),
    path("admission/<uuid:admission_id>/discharge/", views.discharge_patient_action, name="discharge_patient"),
]
