from django.urls import path

from . import allergy_views, views

app_name = "core_patients"

urlpatterns = [
    path("", views.patient_registry_search, name="registry"),
    path("search/", views.patient_registry_search, name="search"),
    path("register/", views.patient_register_view, name="register"),
    path("staff/register/", views.staff_register_view, name="staff_register"),
    path("<uuid:patient_id>/", views.patient_detail_view, name="detail"),
    path("<uuid:patient_id>/edit/", views.patient_edit_view, name="edit"),
    path("<uuid:patient_id>/timeline/", views.patient_timeline_view, name="timeline"),
    path("<uuid:patient_id>/allergies/", allergy_views.allergy_panel_view, name="allergy_panel"),
    path("<uuid:patient_id>/confirm-identity/", views.confirm_identity_view, name="confirm_identity"),
    path("staff/requests/", views.staff_requests_view, name="staff_requests"),
    path("staff/requests/<uuid:request_id>/approve/", views.staff_request_approve_view, name="staff_request_approve"),
    path("staff/requests/<uuid:request_id>/decline/", views.staff_request_decline_view, name="staff_request_decline"),
    path("<uuid:patient_id>/record/", views.patient_record_view, name="record"),
    path("<uuid:patient_id>/allergies/record/", allergy_views.allergy_record_action, name="allergy_record"),
    path("<uuid:patient_id>/allergies/<uuid:allergy_id>/status/", allergy_views.allergy_status_action, name="allergy_status"),
]
