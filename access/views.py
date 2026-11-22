from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from dashboard.audit_actions import AuditAction

from . import services
from .forms import CodeForm, EnrolForm
from .models import AuthorisedBrowser, BackupCode, EmailOTP


def _is_admin(user):
    profile = getattr(user, "staff_profile", None)
    return bool(profile and profile.role == "ADMIN" and profile.is_active and profile.user.is_active)


def _ip(request):
    return services.client_ip(request)


class EnforcedLoginView(LoginView):
    template_name = "registration/login.html"
    # Off on purpose: with it on, Django redirects POSTs too, so a second person would never reach
    # the refusal below. The GET page is still shown; the POST is checked in form_valid.
    redirect_authenticated_user = False

    def form_valid(self, form):
        user = form.get_user()
        browser = getattr(self.request, "authorised_browser", None)
        if browser is None or browser.user_id != user.id:
            services.audit(AuditAction.LOGIN_REFUSED,
                           f"Sign-in for '{user.username}' refused: browser not approved for this account.",
                           ip=_ip(self.request))
            form.add_error(None, "This browser is not approved for this account. "
                                 "Use the browser that was approved for you.")
            return self.form_invalid(form)
        if self.request.user.is_authenticated and self.request.user.pk != user.pk:
            services.audit(AuditAction.LOGIN_REFUSED,
                           f"Sign-in for '{user.username}' refused: another staff member is signed in.",
                           actor=self.request.user, ip=_ip(self.request))
            form.add_error(None, "Another staff member is signed in on this browser. They must sign out first.")
            return self.form_invalid(form)
        response = super().form_valid(form)
        self.request.session.pop("otp_ok", None)
        return response


# ---------- browser proof (called by the browser's script) ----------

@csrf_exempt
@require_GET
def challenge(request):
    response = HttpResponse(services.issue_challenge(), content_type="text/plain")
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_POST
def proof(request):
    """Signature-protected, so it does not need a CSRF token: a forged request cannot sign."""
    browser = getattr(request, "authorised_browser", None)
    if browser is None:
        return HttpResponseForbidden()
    try:
        value = services.accept_proof(browser=browser, nonce=request.POST.get("nonce", ""),
                                      signature=request.POST.get("signature", ""), ip=_ip(request))
    except services.AccessError:
        return HttpResponseForbidden()
    response = HttpResponse("ok", content_type="text/plain")
    response.set_cookie(services.PROOF_COOKIE, value, max_age=services.PROOF_MAX_AGE,
                        httponly=True, secure=not settings.DEBUG, samesite="Strict")
    response["Cache-Control"] = "no-store"
    return response


# ---------- enrolment, approval code and emergency ----------

@require_http_methods(["GET", "POST"])
def enrol(request):
    form = EnrolForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = authenticate(request, username=form.cleaned_data["username"],
                            password=form.cleaned_data["password"])
        if user is not None and getattr(user, "staff_profile", None) and user.is_active:
            services.request_enrolment(user=user, request=request)
        return render(request, "access/enrol_done.html")
    return render(request, "access/enrol.html", {"form": form})


@require_http_methods(["GET", "POST"])
def redeem(request):
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            token, browser = services.redeem_code(
                code=form.cleaned_data["code"],
                public_key_jwk=request.POST.get("public_key", ""),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                ip=_ip(request))
        except services.AccessError as exc:
            form.add_error(None, str(exc))
        else:
            response = redirect("login")
            response.set_cookie(services.COOKIE_NAME, token,
                                max_age=int(services.BROWSER_LIFETIME.total_seconds()),
                                httponly=True, secure=not settings.DEBUG, samesite="Strict")
            return response
    return render(request, "access/redeem.html", {"form": form})


@require_http_methods(["GET", "POST"])
def emergency(request):
    """Admin-only. Password, then an emailed code, then a 24-hour pass shown once."""
    if request.method == "POST" and request.POST.get("action") == "cancel":
        for key in ("emergency_step", "emergency_user"):
            request.session.pop(key, None)
        return redirect("access:emergency")

    step = request.session.get("emergency_step")
    if step is None:
        if request.method == "POST":
            user = authenticate(request, username=request.POST.get("username", ""),
                                password=request.POST.get("password", ""))
            if user is None or not _is_admin(user):
                return render(request, "access/emergency.html",
                              {"step": "password", "error": "Those details are not valid."})
            try:
                services.start_emergency(admin=user, ip=_ip(request))
            except services.AccessError as exc:
                return render(request, "access/emergency.html", {"step": "password", "error": str(exc)})
            request.session["emergency_step"] = "code"
            request.session["emergency_user"] = user.pk
            return render(request, "access/emergency.html", {"step": "code"})
        return render(request, "access/emergency.html", {"step": "password"})

    from django.contrib.auth import get_user_model
    user = get_user_model().objects.filter(pk=request.session.get("emergency_user")).first()
    if user is None or not _is_admin(user):
        request.session.pop("emergency_step", None)
        return redirect("access:emergency")
    if request.method == "POST":
        try:
            services.check_emergency_code(admin=user, code=request.POST.get("code", ""), ip=_ip(request))
        except services.AccessError as exc:
            return render(request, "access/emergency.html", {"step": "code", "error": str(exc)})
        try:
            pass_code = services.issue_emergency_pass(admin=user, ip=_ip(request))
        except services.AccessError as exc:
            for key in ("emergency_step", "emergency_user"):
                request.session.pop(key, None)
            return render(request, "access/emergency.html", {"step": "password", "error": str(exc)})
        for key in ("emergency_step", "emergency_user"):
            request.session.pop(key, None)
        return render(request, "access/emergency.html",
                      {"step": "issued", "pass_code": pass_code, "hours": 24})
    return render(request, "access/emergency.html", {"step": "code"})


# ---------- OTP, backup codes and the first-login enrolment ----------

@require_http_methods(["GET", "POST"])
def otp(request):
    if not request.user.is_authenticated:
        return redirect("login")
    browser = getattr(request, "authorised_browser", None)
    if browser is None or browser.user_id != request.user.id:
        return HttpResponseForbidden()
    next_url = request.GET.get("next") or request.POST.get("next") or "/"
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"
    if services.otp_satisfied(browser=browser, session=request.session):
        return redirect(next_url)

    first_time = not services.is_enrolled(request.user)
    form = CodeForm(request.POST or None)
    if request.method == "GET":
        try:
            if services.send_login_code(user=request.user, browser=browser, ip=_ip(request)):
                messages.info(request, "A sign-in code has been sent to your email.")
        except services.AccessError as exc:
            messages.error(request, str(exc))
    elif form.is_valid():
        try:
            services.verify_login_code(browser=browser, session=request.session,
                                       code=form.cleaned_data["code"], ip=_ip(request))
        except services.AccessError as exc:
            form.add_error(None, str(exc))
        else:
            if first_time:
                request.session["new_backup_codes"] = services.enrol_user(request.user, ip=_ip(request))
                request.session["after_backup_codes"] = next_url
                return redirect("access:backup_codes_show")
            return redirect(next_url)
    return render(request, "access/otp.html", {"form": form, "next": next_url, "first_time": first_time})


@require_POST
def otp_resend(request):
    if not request.user.is_authenticated:
        return redirect("login")
    browser = getattr(request, "authorised_browser", None)
    if browser is None or browser.user_id != request.user.id:
        return HttpResponseForbidden()
    try:
        if services.send_login_code(user=request.user, browser=browser, ip=_ip(request)):
            messages.info(request, "A new sign-in code has been sent.")
        else:
            messages.error(request, "Wait a minute before asking for another code.")
    except services.AccessError as exc:
        messages.error(request, str(exc))
    return redirect("access:otp")


@login_required
@require_http_methods(["GET", "POST"])
def backup_codes(request):
    """Shows how many backup codes remain, and regenerates them on request."""
    if request.method == "POST":
        codes = services.issue_backup_codes(request.user, ip=_ip(request))
        request.session["new_backup_codes"] = codes
        request.session["after_backup_codes"] = reverse("access:backup_codes")
        return redirect("access:backup_codes_show")
    remaining = BackupCode.objects.filter(user=request.user, used_at__isnull=True).count()
    return render(request, "access/backup_codes.html", {"remaining": remaining})


@login_required
@require_GET
def backup_codes_show(request):
    codes = request.session.pop("new_backup_codes", None)
    after = request.session.pop("after_backup_codes", reverse("access:backup_codes"))
    if not codes:
        return redirect("access:backup_codes")
    return render(request, "access/backup_codes_show.html", {"codes": codes, "after": after})


# ---------- administrator screen ----------

@login_required
def browsers(request):
    if not _is_admin(request.user):
        return HttpResponseForbidden()
    rows = AuthorisedBrowser.objects.select_related("user").order_by("status", "-requested_at")[:300]
    return render(request, "access/browsers.html", {"rows": rows})


@login_required
@require_POST
def browser_action(request, pk):
    if not _is_admin(request.user):
        return HttpResponseForbidden()
    browser = AuthorisedBrowser.objects.select_related("user").filter(pk=pk).first()
    if browser is None:
        return HttpResponseForbidden()
    action = request.POST.get("action")
    ip = _ip(request)
    try:
        if action == "approve":
            code = services.approve_browser(browser=browser, admin=request.user, ip=ip)
            return render(request, "access/browsers.html", {
                "rows": AuthorisedBrowser.objects.select_related("user")[:300],
                "issued_code": code, "issued_for": str(browser.user),
                "issued_minutes": int(services.REDEEM_LIFETIME.total_seconds() // 60)})
        if action == "revoke":
            services.revoke_browser(browser=browser, admin=request.user, ip=ip)
        elif action == "unlock":
            services.unlock_browser(browser=browser, admin=request.user, ip=ip)
        else:
            return HttpResponseForbidden()
    except services.AccessError as exc:
        return render(request, "access/browsers.html", {
            "rows": AuthorisedBrowser.objects.select_related("user")[:300], "error": str(exc)})
    return redirect("access:browsers")
