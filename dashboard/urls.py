from django.conf import settings
from django.urls import path

from dashboard.debug_views import url_explorer_view

from . import admin_crud, admin_master_views, views

app_name = "dashboard"

urlpatterns = [
    path("", views.overview_dashboard_view, name="overview"),
    path("accounts/profile/", views.user_profile_view, name="profile"),
    path("audit-logs/", views.audit_log_view, name="audit_logs"),
    path("role-guide/", views.role_guide_view, name="role_guide"),

    # System administrator master panel
    path("admin-master/", admin_master_views.admin_master_view, name="admin_master"),
    path("admin-master/ward/create/", views.admin_create_ward_action, name="admin_create_ward"),
    path("admin-master/bed/create/", views.admin_create_bed_action, name="admin_create_bed"),
    path("admin-master/bed/<uuid:bed_id>/delete/", views.admin_delete_bed_action, name="admin_delete_bed"),
    path("admin-master/patients/<str:pk>/edit/", admin_master_views.admin_patient_edit_view,
         name="admin_patient_edit"),
    path("admin-master/staff/<str:staff_id>/toggle/", admin_master_views.admin_toggle_staff_action,
         name="admin_toggle_staff"),
    path("admin-master/rooms/new/", admin_master_views.admin_room_edit_view, name="admin_room_create"),
    path("admin-master/rooms/<str:pk>/edit/", admin_master_views.admin_room_edit_view, name="admin_room_edit"),
    path("admin-master/rooms/<str:room_id>/toggle/", admin_master_views.admin_room_toggle_action,
         name="admin_room_toggle"),

    # Data manager (generic create, edit and delete)
    path("admin-master/data/", admin_crud.crud_index_view, name="crud_index"),
    path("admin-master/data/<str:app_label>/<str:model_name>/", admin_crud.crud_list_view, name="crud_list"),
    path("admin-master/data/<str:app_label>/<str:model_name>/new/", admin_crud.crud_create_view, name="crud_create"),
    path("admin-master/data/<str:app_label>/<str:model_name>/<str:pk>/edit/", admin_crud.crud_edit_view, name="crud_edit"),
    path("admin-master/data/<str:app_label>/<str:model_name>/<str:pk>/delete/", admin_crud.crud_delete_view, name="crud_delete"),
]

# Development only. The URL explorer lists every route; never expose it in production.
if settings.DEBUG:
    urlpatterns += [
        path("debug/", url_explorer_view, name="url_explorer"),
    ]
