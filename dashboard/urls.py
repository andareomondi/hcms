from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.overview_dashboard_view, name="overview"),
    path("profile/", views.user_profile_view, name="profile"),
    path("audit-logs/", views.audit_log_view, name="audit_logs"),
    path("admin-master/", views.admin_master_dashboard_view, name="admin_master"),
    path("admin-master/ward/create/", views.admin_create_ward_action, name="admin_create_ward"),
    path("admin-master/bed/create/", views.admin_create_bed_action, name="admin_create_bed"),
    path("admin-master/bed/<uuid:bed_id>/delete/", views.admin_delete_bed_action, name="admin_delete_bed"),
]
