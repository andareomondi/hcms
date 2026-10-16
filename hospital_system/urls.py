from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    
    # App inclusions
    path("patients/", include("core_patients.urls")),
    path("triage/", include("triage_queue.urls")),
    path("doctors/", include("doctors_portal.urls")),
    path("pharmacy/", include("pharmacy.urls")),
    path("radiology/", include("radiology.urls")),
    path("wards/", include("wards.urls")),
    path("billing/", include("billing.urls")),
    
    # Root dashboard app
    path("", include("dashboard.urls")),
]
