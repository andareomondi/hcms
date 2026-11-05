from django.apps import AppConfig


class CorePatientsConfig(AppConfig):
    name = "core_patients"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import signals  # noqa: F401
