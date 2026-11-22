from django.apps import AppConfig


class AccessConfig(AppConfig):
    name = "access"
    verbose_name = "Browser authorisation and OTP"

    def ready(self):
        from . import signals  # noqa: F401  (connects the sign-in audit and alert handlers)
