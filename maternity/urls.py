from django.urls import path

from . import views

app_name = "maternity"

urlpatterns = [
    path("", views.maternity_index_view, name="index"),
    path("encounter/<uuid:encounter_id>/", views.delivery_view, name="delivery"),
    path("child/<uuid:patient_id>/", views.child_view, name="child"),
    path("temporary-children/", views.temporary_children_view, name="temporary_children"),
    path("patients/<uuid:patient_id>/labour/admit/", views.labour_admit_action, name="labour_admit"),
    path("labour/", views.labour_board_view, name="labour_board"),
    path("labour/<uuid:stay_id>/", views.labour_record_view, name="labour_record"),
    path("labour/<uuid:stay_id>/birth/", views.labour_birth_view, name="labour_birth"),
    path("labour/<uuid:stay_id>/move-to-ward/", views.move_to_ward_view, name="move_to_ward"),
    path("episode/<uuid:episode_id>/sign-off/", views.episode_signoff_action, name="episode_signoff"),
    path("child/<uuid:patient_id>/name-correction/", views.name_correction_request_view,
         name="name_correction_request"),
    path("name-corrections/", views.name_corrections_view, name="name_corrections"),
    path("name-corrections/<uuid:correction_id>/confirm/", views.name_correction_confirm_action,
         name="name_correction_confirm"),
    path("episode/<uuid:episode_id>/after-birth-report/", views.after_birth_report_view, name="after_birth_report"),
    path("child/<uuid:patient_id>/vaccinate/", views.vaccination_action, name="vaccinate"),
    path("child/<uuid:patient_id>/birth-certificate/", views.confirm_birth_certificate_action, name="confirm_birth_certificate"),
]
