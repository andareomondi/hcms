from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect
from django.urls import include, path

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action
from duty.commitments import open_commitments
from duty.services import clock_out, current_session
from hospital_system.views_pwa import service_worker


class CustomLogoutView(auth_views.LogoutView):
    """
    Logs a staff member out and clocks them out automatically, but only when they have
    no open commitments. A doctor with assigned patients or open ward stays stays signed
    in and is told what to hand over. Nothing is swallowed silently.
    """
    def post(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request, "staff_profile", None)
            blockers = open_commitments(profile)
            if blockers:
                messages.error(request, "You cannot log out yet: " + "; ".join(blockers) +
                               ". Hand them over first.")
                return redirect(request.META.get("HTTP_REFERER") or "/")
            if profile and current_session(profile):
                clock_out(profile)
                log_action(request, AuditAction.DUTY_CLOCKED_OUT,
                           f"{profile} clocked out automatically on logout.")
        return super().post(request, *args, **kwargs)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html", redirect_authenticated_user=True), name="login"),
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
    path("sw.js", service_worker, name="service_worker"),
    
    # Root dashboard app
    path("", include("dashboard.urls")),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
