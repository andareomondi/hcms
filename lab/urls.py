from django.urls import path

from . import views

app_name = "lab"

urlpatterns = [
    path("", views.lab_dashboard_view, name="dashboard"),
    path("order/<uuid:encounter_id>/", views.order_tests_action, name="order"),
    path("orders/<uuid:order_id>/receive/", views.receive_order_action, name="receive"),
    path("orders/<uuid:order_id>/results/", views.result_entry_view, name="results"),
    path("orders/<uuid:order_id>/results/save/", views.save_results_action, name="save_results"),
    path("orders/<uuid:order_id>/report/", views.submit_report_action, name="submit_report"),
    path("orders/<uuid:order_id>/verify/", views.verify_view, name="verify"),
    path("orders/<uuid:order_id>/verify/release/", views.verify_release_action, name="verify_release"),
    path("orders/<uuid:order_id>/print/", views.order_print_view, name="print"),
    path("lines/<uuid:line_id>/cancel/", views.cancel_line_action, name="cancel_line"),
    path("lines/<uuid:line_id>/amend/", views.amend_result_action, name="amend"),
]

