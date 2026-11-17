from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@never_cache
@require_GET
def service_worker(request):
    """
    Serves sw.js from the site root so it controls every page.
    Service-Worker-Allowed: "/" confirms the scope.
    """
    path = Path(settings.BASE_DIR) / "static" / "js" / "sw.js"
    response = HttpResponse(path.read_text(encoding="utf-8"),
                            content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    return response
