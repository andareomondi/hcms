from django.urls import path
from . import views

app_name = "pharmacy"

urlpatterns = [
    path("medication/search/", views.medication_search_view, name="medication_search"),
    path("worklist/nurse/", views.nurse_worklist_view, name="nurse_worklist"),
    path("administer/<uuid:order_id>/modal/", views.administer_modal_view, name="administer_modal"),
    path("administer/<uuid:order_id>/post/", views.record_administration_action, name="administer"),
]
