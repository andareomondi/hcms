from django.urls import path
from . import views

app_name = "wards"

urlpatterns = [
    path("beds/", views.bed_grid_view, name="bed_grid"),
    path("bed/<uuid:bed_id>/admit/", views.admit_patient_view, name="admit"),
    path("admission/<uuid:admission_id>/discharge/", views.discharge_patient_view, name="discharge"),
]
