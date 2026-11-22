from urllib.parse import quote

from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import redirect

from . import services

# Reachable by anyone. Enrolment, approval-code entry, emergency page and static files.
PUBLIC_PREFIXES = ("/static/", "/access/enrol/", "/access/redeem/", "/access/emergency/")
# Need an approved browser but not yet a browser proof. These issue the proof.
PROOF_EXEMPT_PREFIXES = ("/access/challenge/", "/access/proof/")
# Need proof, but not the OTP step.
OTP_EXEMPT_PREFIXES = ("/access/otp/", "/accounts/logout/", "/static/")

# A bare 403 with no information. Used for browsers that are not approved.
REFUSED_BODY = b""
# A 403 page with no site information. Used for an approved browser whose proof has expired, so the
# script can refresh it and reload. A copied cookie in another browser cannot complete the refresh.
CHECK_PAGE = (b'<!doctype html><html><head><meta charset="utf-8"><title>Checking this browser</title>'
              b'</head><body data-hcms-check><p>Checking this browser...</p>'
              b'<script src="/static/js/hcms_proof.js"></script></body></html>')


def _refused():
    return HttpResponseForbidden(REFUSED_BODY)


def _check_page():
    response = HttpResponseForbidden(CHECK_PAGE, content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-store"
    return response


class BrowserAuthorisationMiddleware:
    """
    1. A browser without an approved cookie and key gets a bare 403.
    2. An approved browser must also present a valid proof, which the browser refreshes by signing a
       challenge with its private key. Without the key, a copied cookie cannot get a proof.
    3. The signed-in user must match the browser, and must have passed the OTP step (or be in the
       12-hour trust window).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "BROWSER_AUTHORISATION_ENFORCED", True):
            return self.get_response(request)
        path = request.path
        if path.startswith(PUBLIC_PREFIXES):
            return self.get_response(request)

        browser = services.browser_for_request(request)
        if browser is None:
            return _refused()

        services.note_ip(browser=browser, ip=services.client_ip(request), session=request.session)
        request.authorised_browser = browser

        if path.startswith(PROOF_EXEMPT_PREFIXES):
            return self.get_response(request)

        if not services.proof_valid(request, browser):
            return _check_page()

        if request.user.is_authenticated:
            if browser.user_id != request.user.id:
                return _refused()
            if not path.startswith(OTP_EXEMPT_PREFIXES) and not services.otp_satisfied(
                    browser=browser, session=request.session):
                return redirect(f"/access/otp/?next={quote(path)}")

        return self.get_response(request)
