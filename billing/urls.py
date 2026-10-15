from django.urls import path
from . import views

app_name = "billing"

urlpatterns = [
    path("provisional/", views.provisional_review_view, name="provisional_review"),
    path("charge/<uuid:charge_id>/post/", views.post_charge_action, name="post_charge"),
    path("invoice/<uuid:encounter_id>/", views.invoice_detail_view, name="invoice"),
]
