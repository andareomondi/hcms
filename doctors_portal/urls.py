from django.urls import path
from . import views

app_name = "doctors_portal"

urlpatterns = [
    path("workstation/<uuid:encounter_id>/", views.doctor_workstation_view, name="workstation"),
    path("encounter/<uuid:encounter_id>/note/save/", views.save_clinical_note, name="save_note"),
]
