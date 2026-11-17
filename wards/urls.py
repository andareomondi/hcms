from django.urls import path

from . import views

app_name = "wards"

urlpatterns = [
    path("", views.ward_dashboard_view, name="dashboard"),
    path("admission/<uuid:admission_id>/request-discharge/", views.request_discharge_action,
         name="request_discharge"),
    path("admission/<uuid:admission_id>/approve-discharge/", views.approve_discharge_action,
         name="approve_discharge"),
    path("beds/<uuid:bed_id>/status/", views.change_bed_status_action, name="update_bed_status"),
    path("beds/<uuid:bed_id>/history/", views.bed_history_view, name="bed_history"),
    path("occupancy/", views.ward_occupancy_view, name="occupancy"),
]
