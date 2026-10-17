from django.urls import path

from . import views

app_name = "duty"

urlpatterns = [
    path("board/", views.board_view, name="board"),
    path("clock-in/", views.clock_in_view, name="clock_in"),
    path("clock-in/submit/", views.clock_in_action, name="clock_in_submit"),
    path("clock-out/", views.clock_out_action, name="clock_out"),
    path("room/", views.change_room_action, name="change_room"),
]
