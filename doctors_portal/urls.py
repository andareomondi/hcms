from django.urls import path

from . import views

app_name = "doctors_portal"
urlpatterns = [
    path("", views.doctor_dashboard_view, name="dashboard"),
    path("consultation/<uuid:encounter_id>/", views.consultation_room_view, name="consultation_room"),
    path("consultation/<uuid:encounter_id>/note/", views.add_clinical_note_action, name="add_note"),
    path("consultation/<uuid:encounter_id>/prescribe/", views.prescribe_medication_action, name="prescribe"),
    path("consultation/<uuid:encounter_id>/admit/", views.admit_patient_action, name="admit"),
    path("consultation/<uuid:encounter_id>/complete/", views.complete_consultation_action, name="complete"),
    path("workstation/<uuid:encounter_id>/", views.doctor_workstation_view, name="workstation"),
    path("workstation/<uuid:encounter_id>/note/", views.save_clinical_note, name="save_note"),
    path("call-next/", views.call_next_patient_action, name="call_next"),
    path("consultation/<uuid:encounter_id>/request-scan/", views.request_scan_action, name="request_radiology"),
]
