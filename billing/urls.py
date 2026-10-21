from django.urls import path

from . import credit_views, views

app_name = "billing"

urlpatterns = [
    path("", views.billing_dashboard_view, name="dashboard"),
    path("provisional-review/", views.billing_dashboard_view, name="provisional_review"),
    path("charge/<uuid:charge_id>/post/", views.post_charge_action, name="post_charge"),
    path("charge/<uuid:charge_id>/pay/", views.mark_charge_paid_action, name="mark_paid"),
    path("bills-ready/", views.bills_ready_view, name="bills_ready"),
    path("encounter/<uuid:encounter_id>/", views.encounter_bill_view, name="encounter"),
    path("encounter/<uuid:encounter_id>/post/", views.post_encounter_bill_action, name="post_encounter"),
    path("encounter/<uuid:encounter_id>/pay/", views.take_payment_action, name="take_payment"),
    path("payments/<uuid:payment_id>/receipt/", views.payment_receipt_view, name="payment_receipt"),
    path("account/<uuid:account_id>/", views.account_statement_view, name="account"),
    path("credit/<str:kind>/<uuid:object_id>/", credit_views.approve_credit_action, name="approve_credit"),
    path("bypass/", credit_views.bypass_queue_view, name="bypass_queue"),
]
