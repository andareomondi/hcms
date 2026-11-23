from django.urls import path

from . import views

app_name = "surgery"

urlpatterns = [
    path("", views.case_list_view, name="list"),
    path("book/<uuid:patient_id>/", views.book_view, name="book"),
    path("case/<uuid:case_id>/", views.case_view, name="case"),
    path("case/<uuid:case_id>/do/<str:action>/", views.case_action_view, name="action"),
    path("stock/", views.stock_view, name="stock"),
]
