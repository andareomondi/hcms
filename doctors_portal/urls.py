from django.urls import path
from . import views

app_name = "doctors_portal"

urlpatterns = [
    path("", views.doctor_dashboard_view, name="dashboard"),
    path("call-next/", views.call_next_patient_action, name="call_next"),
    path("workstation/<uuid:encounter_id>/", views.doctor_workstation_view, name="workstation"),
    path("workstation/<uuid:encounter_id>/note/", views.save_clinical_note, name="save_note"),
]
