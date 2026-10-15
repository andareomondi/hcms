from django.urls import path
from . import views

app_name = "triage_queue"

urlpatterns = [
    path("board/", views.triage_board_view, name="board"),
    path("board/fragment/", views.triage_board_fragment, name="board_fragment"),
    path("encounter/new/<uuid:patient_id>/", views.encounter_create_view, name="encounter_create"),
]
