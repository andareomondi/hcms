from django.urls import path

from . import views

app_name = "radiology"

urlpatterns = [
    path("", views.radiology_dashboard_view, name="dashboard"),
    path("worklist/", views.radiology_dashboard_view, name="worklist"),
    path("order/<uuid:order_id>/report/", views.save_radiology_report, name="save_report"),
]
