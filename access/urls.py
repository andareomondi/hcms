from django.urls import path

from . import views

app_name = "access"

urlpatterns = [
    path("enrol/", views.enrol, name="enrol"),
    path("redeem/", views.redeem, name="redeem"),
    path("otp/", views.otp, name="otp"),
    path("otp/resend/", views.otp_resend, name="otp_resend"),
    path("challenge/", views.challenge, name="challenge"),
    path("proof/", views.proof, name="proof"),
    path("backup-codes/", views.backup_codes, name="backup_codes"),
    path("backup-codes/show/", views.backup_codes_show, name="backup_codes_show"),
    path("emergency/", views.emergency, name="emergency"),
    path("browsers/", views.browsers, name="browsers"),
    path("browsers/<int:pk>/action/", views.browser_action, name="browser_action"),
]
