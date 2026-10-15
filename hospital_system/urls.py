"""
URL configuration for HCMS project.
Routes requests across the modular monolith applications.
"""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    # Django Admin (Restricted to internal system admins)
    path("admin/", admin.site.urls),

    # Authentication
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),

    # Core Patient Registry, Identity, Timeline, and Device Enrolment
    path("patients/", include("core_patients.urls")),

    # Triage and Waiting Queue
    path("triage/", include("triage_queue.urls")),

    # Physician Workstation & Consultations
    path("doctor/", include("doctors_portal.urls")),

    # Pharmacy, Stock, Dispensing, and Nurse Administration Worklists
    path("pharmacy/", include("pharmacy.urls")),

    # Radiology Orders and Imaging Studies
    path("radiology/", include("radiology.urls")),

    # In-patient Wards and Bed Grid
    path("wards/", include("wards.urls")),

    # Billing, Provisional Review, and Invoicing
    path("billing/", include("billing.urls")),
]
