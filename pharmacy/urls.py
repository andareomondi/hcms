from django.urls import path

from . import views

app_name = "pharmacy"

urlpatterns = [
    path("", views.pharmacy_dashboard_view, name="dashboard"),
    path("medication/new/", views.medication_create_or_edit_view, name="medication_create"),
    path("medication/<uuid:medication_id>/edit/", views.medication_create_or_edit_view, name="medication_edit"),
    path("medication/<uuid:medication_id>/receive/", views.stock_receive_action, name="stock_receive"),
    path("medication/<uuid:medication_id>/adjust/", views.stock_adjust_action, name="stock_adjust"),
    path("dispense/", views.dispense_queue_view, name="dispense_queue"),
    path("dispense/<uuid:order_id>/", views.dispense_action, name="dispense_action"),
    path("worklist/", views.nurse_worklist_view, name="nurse_worklist"),
    path("worklist/<uuid:order_id>/administer/", views.administer_action, name="administer_action"),
]
