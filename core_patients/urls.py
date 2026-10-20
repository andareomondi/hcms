from django.urls import path

from core_patients import merge_views
from core_patients.debug_views import url_explorer_view

from . import allergy_views, consent_views, family_views, views

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
    path("<uuid:patient_id>/family/", family_views.family_view, name="family"),
    path("<uuid:patient_id>/family/register-child/", family_views.register_child_action, name="register_child"),
    path("<uuid:patient_id>/family/link/", family_views.link_guardian_action, name="link_guardian"),
    path("<uuid:patient_id>/consent/", consent_views.consent_page_view, name="consent"),
    path("<uuid:patient_id>/consent/record/", consent_views.consent_record_action, name="consent_record"),
    path("<uuid:patient_id>/family/search/", family_views.family_search_view, name="family_search"),
    path("<uuid:patient_id>/merge/", merge_views.merge_page_view, name="merge"),
    path("<uuid:patient_id>/merge/search/", merge_views.merge_search_view, name="merge_search"),
    path("<uuid:patient_id>/merge/", merge_views.merge_action, name="merge_action"),
    path("<uuid:patient_id>/merge/", merge_views.merge_page_view, name="merge"),
    path("<uuid:patient_id>/merge/search/", merge_views.merge_search_view, name="merge_search"),
    path("<uuid:patient_id>/merge/submit/", merge_views.merge_action, name="merge_action"),
    path("debug/", url_explorer_view, name="url_explorer"),
]
