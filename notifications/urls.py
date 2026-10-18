from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("dropdown/", views.dropdown_view, name="dropdown"),
    path("count/", views.count_view, name="count"),
    path("<uuid:notification_id>/read/", views.mark_read_view, name="mark_read"),
    path("read-all/", views.mark_all_read_view, name="mark_all_read"),
]
