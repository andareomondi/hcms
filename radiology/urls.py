from django.urls import path
from . import views

app_name = "radiology"

urlpatterns = [
    path("worklist/", views.radiology_worklist_view, name="worklist"),
    path("order/<uuid:order_id>/report/", views.save_radiology_report, name="save_report"),
]
