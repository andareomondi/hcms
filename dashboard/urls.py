from django.urls import path

from . import admin_crud, views

app_name = "dashboard"

urlpatterns = [
    path("", views.overview_dashboard_view, name="overview"),
    path("profile/", views.user_profile_view, name="profile"),
    path("audit-logs/", views.audit_log_view, name="audit_logs"),
    path("admin-master/", views.admin_master_dashboard_view, name="admin_master"),
    path("admin-master/ward/create/", views.admin_create_ward_action, name="admin_create_ward"),
    path("admin-master/bed/create/", views.admin_create_bed_action, name="admin_create_bed"),
    path("admin-master/bed/<uuid:bed_id>/delete/", views.admin_delete_bed_action, name="admin_delete_bed"),
    path("admin-master/data/", admin_crud.crud_index_view, name="crud_index"),
    path("admin-master/data/<str:app_label>/<str:model_name>/", admin_crud.crud_list_view, name="crud_list"),
    path("admin-master/data/<str:app_label>/<str:model_name>/new/", admin_crud.crud_create_view, name="crud_create"),
    path("admin-master/data/<str:app_label>/<str:model_name>/<str:pk>/edit/", admin_crud.crud_edit_view, name="crud_edit"),
    path("admin-master/data/<str:app_label>/<str:model_name>/<str:pk>/delete/", admin_crud.crud_delete_view, name="crud_delete"),
    path("role-guide/", views.role_guide_view, name="role_guide"),
]
