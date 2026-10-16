from django.urls import path
from . import views

app_name = "pharmacy"

urlpatterns = [
    path("", views.pharmacy_dashboard_view, name="dashboard"),
    path("medication/new/", views.medication_create_or_edit_view, name="medication_create"),
    path("medication/<uuid:medication_id>/edit/", views.medication_create_or_edit_view, name="medication_edit"),
    path("worklist/", views.nurse_worklist_view, name="nurse_worklist"),
    path("worklist/<uuid:order_id>/administer/", views.mark_administered_action, name="mark_administered"),
]
