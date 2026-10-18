from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("", views.billing_dashboard_view, name="dashboard"),
    path("provisional-review/", views.billing_dashboard_view, name="provisional_review"), # alias for navbar
    path("charge/<uuid:charge_id>/post/", views.post_charge_action, name="post_charge"),
    path("charge/<uuid:charge_id>/pay/", views.mark_charge_paid_action, name="mark_paid"),
    path("bills-ready/", views.bills_ready_view, name="bills_ready"),
    path("encounter/<uuid:encounter_id>/", views.encounter_bill_view, name="encounter"),
    path("encounter/<uuid:encounter_id>/post/", views.post_encounter_bill_action, name="post_encounter"),
]
