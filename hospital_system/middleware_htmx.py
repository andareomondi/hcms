from django.conf import settings
from django.http import HttpResponse


class HtmxSessionExpiredMiddleware:
    """
    When an htmx request is redirected to login (session expired), tell htmx to
    navigate the whole page to login instead of swapping the login page into a fragment.
    """
    REDIRECT_CODES = (301, 302, 303, 307, 308)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.headers.get("HX-Request") and response.status_code in self.REDIRECT_CODES:
            location = response.get("Location", "")
            if settings.LOGIN_URL in location:
                expired = HttpResponse(status=401)
                expired["HX-Redirect"] = location
                return expired
        return response
