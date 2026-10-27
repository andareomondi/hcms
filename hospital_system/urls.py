from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from duty.services import clock_out, current_session


class CustomLogoutView(auth_views.LogoutView):
    """Automatically clocks out the staff member on logout, logs the audit action, and clears the session."""
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request, "staff_profile", None)
            if profile and current_session(profile):
                try:
                    clock_out(profile)
                    # Log the automatic clock-out action using the system's auditing utility
                    log_action(request, AuditAction.DUTY_CLOCKED_OUT, f"{profile} clocked out automatically on logout.")
                except Exception:
                    # Fallback to ensure logout always succeeds even if a validation rule blocks it
                    pass
        return super().dispatch(request, *args, **kwargs)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/logout/", CustomLogoutView.as_view(), name="logout"),
    
    # App inclusions
    path("patients/", include("core_patients.urls")),
    path("triage/", include("triage_queue.urls")),
    path("doctors/", include("doctors_portal.urls")),
    path("pharmacy/", include("pharmacy.urls")),
    path("radiology/", include("radiology.urls")),
    path("wards/", include("wards.urls")),
    path("billing/", include("billing.urls")),
    path("duty/", include("duty.urls")),
    path("notifications/", include("notifications.urls")),
    path("maternity/", include("maternity.urls")),
    path("lab/", include("lab.urls")),
    
    # Root dashboard app
    path("", include("dashboard.urls")),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
