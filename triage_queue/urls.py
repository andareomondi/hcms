from django.urls import path

from . import views

app_name = "triage_queue"

urlpatterns = [
    path("board/", views.triage_board_view, name="board"),
    path("board/fragment/", views.triage_board_fragment, name="board_fragment"),
    path("encounter/new/<uuid:patient_id>/", views.open_visit_view, name="encounter_create"),
    path("encounter/<uuid:encounter_id>/triage/", views.triage_visit_view, name="triage_visit"),
    path("entry/<uuid:entry_id>/reassign/", views.reassign_entry_action, name="reassign"),
]
