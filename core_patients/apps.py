import os
import sys
import threading

from django.apps import AppConfig


class CorePatientsConfig(AppConfig):
    name = "core_patients"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import signals  # noqa: F401

        if "runserver" in sys.argv or "gunicorn" in sys.argv or "uvicorn" in sys.argv:
            if os.environ.get("RUN_MAIN") == "true" or "runserver" not in sys.argv:
                from . import scheduler
                # Wait 2 seconds so Django finishes app initialization before DB queries run
                timer = threading.Timer(2.0, scheduler.start)
                timer.daemon = True
                timer.start()

