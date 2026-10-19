from django.urls import path

from . import views

app_name = "maternity"

urlpatterns = [
    path("", views.maternity_index_view, name="index"),
    path("encounter/<uuid:encounter_id>/", views.delivery_view, name="delivery"),
    path("child/<uuid:patient_id>/", views.child_view, name="child"),
    path("child/<uuid:patient_id>/vaccinate/", views.vaccination_action, name="vaccinate"),
    path("child/<uuid:patient_id>/birth-certificate/", views.confirm_birth_certificate_action, name="confirm_birth_certificate"),
]
