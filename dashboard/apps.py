from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "dashboard"

    def ready(self):
        from django.contrib import admin

        import dashboard.signals  # noqa: F401

        # Only active superusers may use the Django admin at /admin/.
        # Plain is_staff accounts are refused even if they have model permissions.
        admin.site.has_permission = lambda request: request.user.is_active and request.user.is_superuser
