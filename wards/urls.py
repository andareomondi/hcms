from django.urls import path

from . import views

app_name = "wards"

urlpatterns = [
    path("", views.ward_dashboard_view, name="dashboard"),
    path("bed/<uuid:bed_id>/assign/", views.assign_bed_action, name="assign_bed"),
    path("admission/<uuid:admission_id>/discharge/", views.discharge_patient_action, name="discharge_patient"),
    path("beds/<uuid:bed_id>/status/", views.change_bed_status_action, name="update_bed_status"),
    path("beds/<uuid:bed_id>/history/", views.bed_history_view, name="bed_history"),
    path("occupancy/", views.ward_occupancy_view, name="occupancy"),
]
