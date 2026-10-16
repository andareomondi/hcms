from django.urls import path
from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.overview_dashboard_view, name="overview"),
    path("profile/", views.user_profile_view, name="profile"),
    path("audit-logs/", views.audit_log_view, name="audit_logs"),
]
