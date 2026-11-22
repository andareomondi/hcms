"""
All browser-authorisation, OTP, backup-code, audit and alert rules. Views, middleware and signals
call these functions; nothing else decides access.
"""
import hashlib
import hmac
import secrets
import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core_patients.models import StaffProfile
from dashboard.audit_actions import AuditAction
from dashboard.utils import create_audit_entry
from notifications.services import notify

from . import crypto
from .models import AuthorisedBrowser, BackupCode, EmailOTP, EmergencyAllowance, EmergencyPass, OTPEnrolment

COOKIE_NAME = "hcms_browser"
PROOF_COOKIE = "hcms_proof"
OTP_LIFETIME = timedelta(minutes=2)
OTP_RESEND_WAIT = timedelta(seconds=60)
OTP_MAX_FAILURES = 3
TRUST_DURATION = timedelta(hours=12)
BROWSER_LIFETIME = timedelta(days=90)
REDEEM_LIFETIME = timedelta(minutes=30)
EMERGENCY_PASS_LIFETIME = timedelta(hours=24)
PROOF_LIFETIME = timedelta(minutes=5)
PROOF_MAX_AGE = int(PROOF_LIFETIME.total_seconds())
CHALLENGE_WINDOW_SECONDS = 120
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW = 15 * 60
BACKUP_CODE_COUNT = 10
BACKUP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class AccessError(Exception):
    """A refusal with a message the user can act on."""


# ---------- primitives ----------

def _hmac(text):
    return hmac.new(settings.SECRET_KEY.encode(), text.encode(), hashlib.sha256).hexdigest()


def _hash(value):
    return _hmac(value)


def _now():
    return timezone.now()


def client_ip(request):
    """Trusts X-Forwarded-For only when a proxy is declared. Otherwise the socket address is used."""
    if getattr(settings, "TRUST_X_FORWARDED_FOR", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def audit(action, description, *, actor=None, ip=None):
    create_audit_entry(actor=actor, action_type=action, description=description[:500], ip_address=ip)


def alert_admins(message, *, action=None, ip=None):
    """In-app notification and email to every active administrator, plus an audit entry."""
    profiles = list(StaffProfile.objects.filter(role="ADMIN", is_active=True).select_related("user"))
    notify(profiles, kind="SECURITY_ALERT", message=message, link=reverse("access:browsers"))
    emails = [p.user.email for p in profiles if p.user.email]
    if emails:
        send_mail("HCMS security alert", message, None, emails, fail_silently=True)
    if action:
        audit(action, message, ip=ip)


# ---------- browser lookup and proof ----------

def browser_for_request(request):
    """The active browser for this request's cookie, with a key, not expired. Otherwise None."""
    token = request.COOKIES.get(COOKIE_NAME)
    if not token:
        return None
    browser = (AuthorisedBrowser.objects.select_related("user")
               .filter(token_hash=_hash(token), status=AuthorisedBrowser.ACTIVE).first())
    if browser is None or not browser.public_key_jwk:
        return None
    if browser.expires_at is None or browser.expires_at <= _now():
        return None
    return browser


def issue_challenge():
    body = f"{int(time.time())}.{secrets.token_urlsafe(16)}"
    return f"{body}.{_hmac('challenge|' + body)}"


def accept_proof(*, browser, nonce, signature, ip=None):
    """
    Checks a signed challenge against this browser's stored public key, then issues a short proof.
    A copied cookie cannot pass here, because the private key is only in the browser.
    Returns the proof cookie value.
    """
    parts = (nonce or "").split(".")
    if len(parts) != 3:
        _refuse_proof(browser, "malformed challenge", ip)
    body = f"{parts[0]}.{parts[1]}"
    if not hmac.compare_digest(parts[2], _hmac("challenge|" + body)):
        _refuse_proof(browser, "forged challenge", ip)
    try:
        issued = int(parts[0])
    except ValueError:
        _refuse_proof(browser, "malformed challenge", ip)
    if abs(time.time() - issued) > CHALLENGE_WINDOW_SECONDS:
        _refuse_proof(browser, "expired challenge", ip)
    public_key = crypto.public_key_from_stored(browser.public_key_jwk)
    if not crypto.verify_signature(public_key, nonce.encode(), signature):
        _refuse_proof(browser, "bad signature", ip)
    if not cache.add(f"chal:{parts[1]}", 1, timeout=CHALLENGE_WINDOW_SECONDS):
        _refuse_proof(browser, "replayed challenge", ip)
    expires = int((_now() + PROOF_LIFETIME).timestamp())
    return f"{browser.pk}.{expires}.{_hmac(f'proof|{browser.pk}|{expires}')}"


def _refuse_proof(browser, reason, ip):
    audit(AuditAction.PROOF_FAILED, f"Browser check failed ({reason}).", actor=browser.user, ip=ip)
    raise AccessError("This browser could not be verified.")


def proof_valid(request, browser):
    raw = request.COOKIES.get(PROOF_COOKIE, "")
    parts = raw.split(".")
    if len(parts) != 3 or parts[0] != str(browser.pk):
        return False
    try:
        expires = int(parts[1])
    except ValueError:
        return False
    if expires <= int(_now().timestamp()):
        return False
    return hmac.compare_digest(parts[2], _hmac(f"proof|{browser.pk}|{expires}"))


# ---------- approval and redemption ----------

def request_enrolment(*, user, request):
    """Public request. The caller always shows the same reply, so nothing is revealed."""
    ip = client_ip(request)
    ua = (request.META.get("HTTP_USER_AGENT") or "")[:300]
    recent = AuthorisedBrowser.objects.filter(
        user=user, status=AuthorisedBrowser.PENDING, enrolled_ip=ip,
        requested_at__gte=_now() - timedelta(hours=24)).exists()
    if not recent:
        AuthorisedBrowser.objects.create(user=user, enrolled_ip=ip, user_agent=ua)
        audit(AuditAction.BROWSER_REQUESTED, f"Browser approval requested from {ip}.", actor=user, ip=ip)


@transaction.atomic
def approve_browser(*, browser, admin, bootstrap=False, ip=None):
    """
    Approves a pending browser and returns the one-time code. `bootstrap` is used only by the developer
    command for the first browser, when no other active browser exists.
    """
    browser = AuthorisedBrowser.objects.select_for_update().get(pk=browser.pk)
    if browser.status != AuthorisedBrowser.PENDING:
        raise AccessError("This request is no longer waiting for approval.")
    if browser.user_id == admin.id and not bootstrap:
        raise AccessError("An administrator cannot approve their own browser.")
    code = secrets.token_urlsafe(9)
    browser.status = AuthorisedBrowser.AWAITING_CODE
    browser.redeem_hash = _hash(code)
    browser.redeem_expires_at = _now() + REDEEM_LIFETIME
    browser.approved_by = admin
    browser.approved_at = _now()
    browser.save()
    audit(AuditAction.BROWSER_APPROVED, f"Approved a browser for {browser.user}.", actor=admin, ip=ip)
    return code


@transaction.atomic
def revoke_browser(*, browser, admin, ip=None):
    browser = AuthorisedBrowser.objects.select_for_update().get(pk=browser.pk)
    if browser.status == AuthorisedBrowser.REVOKED:
        raise AccessError("This browser is already revoked.")
    browser.status = AuthorisedBrowser.REVOKED
    browser.revoked_at = _now()
    browser.revoked_by = admin
    browser.save(update_fields=["status", "revoked_at", "revoked_by"])
    audit(AuditAction.BROWSER_REVOKED, f"Revoked a browser of {browser.user}.", actor=admin, ip=ip)


@transaction.atomic
def unlock_browser(*, browser, admin, ip=None):
    browser = AuthorisedBrowser.objects.select_for_update().get(pk=browser.pk)
    if browser.status != AuthorisedBrowser.LOCKED:
        raise AccessError("This browser is not locked.")
    browser.status = AuthorisedBrowser.ACTIVE
    browser.otp_failures = 0
    browser.save(update_fields=["status", "otp_failures"])
    audit(AuditAction.BROWSER_UNLOCKED, f"Unlocked a browser of {browser.user}.", actor=admin, ip=ip)


def _valid_public_key(public_key_jwk):
    try:
        return crypto.normalise_public_key(public_key_jwk)
    except crypto.KeyError_ as exc:
        raise AccessError(str(exc))


def redeem_code(*, code, public_key_jwk, user_agent="", ip=None):
    """
    Turns a one-time code into an active browser. The code is an approval code, or an emergency pass.
    The browser's public key must be supplied. Returns (token, browser).
    """
    code = (code or "").strip()
    if not code:
        raise AccessError("Enter the code.")
    key = _valid_public_key(public_key_jwk)
    digest = _hash(code)
    token = secrets.token_urlsafe(32)
    refused = None
    with transaction.atomic():
        result = _redeem_in_transaction(code, digest, key, token, user_agent, ip)
        if isinstance(result, str):
            refused = result
    if refused is not None:
        audit(AuditAction.EMERGENCY_REFUSED if refused == "expired pass" else AuditAction.BROWSER_REFUSED,
              "An expired emergency pass was tried." if refused == "expired pass"
              else "An unknown or used code was entered.", ip=ip)
        raise AccessError("This emergency code has expired." if refused == "expired pass"
                          else "That code is not valid. Check it and try again.")
    return result


def _redeem_in_transaction(code, digest, key, token, user_agent, ip):
    """Returns (token, browser) on success, or a short reason string for a refusal."""
    browser = (AuthorisedBrowser.objects.select_for_update()
               .filter(redeem_hash=digest, status=AuthorisedBrowser.AWAITING_CODE).first())
    if browser is not None:
        if browser.redeem_expires_at is None or browser.redeem_expires_at <= _now():
            raise AccessError("This code has expired. Ask the administrator for a new one.")
        browser.status = AuthorisedBrowser.ACTIVE
        browser.token_hash = _hash(token)
        browser.public_key_jwk = key
        browser.redeem_hash = ""
        browser.redeem_expires_at = None
        browser.expires_at = _now() + BROWSER_LIFETIME
        browser.last_ip = ip
        browser.user_agent = user_agent[:300]
        browser.save()
        audit(AuditAction.BROWSER_REDEEMED, "Browser authorised with an approval code.",
              actor=browser.user, ip=ip)
        return token, browser

    emergency = (EmergencyPass.objects.select_for_update()
                 .filter(token_hash=digest, used=False).first())
    if emergency is not None:
        if emergency.expires_at <= _now():
            return "expired pass"
        emergency.used = True
        emergency.save(update_fields=["used"])
        browser = AuthorisedBrowser.objects.create(
            user=emergency.admin, status=AuthorisedBrowser.ACTIVE, token_hash=_hash(token),
            public_key_jwk=key, expires_at=_now() + BROWSER_LIFETIME, last_ip=ip, is_emergency=True,
            user_agent=user_agent[:300], approved_by=emergency.admin, approved_at=_now())
        alert_admins(f"An emergency pass was used by {emergency.admin} from {ip}.",
                     action=AuditAction.EMERGENCY_PASS_USED, ip=ip)
        return token, browser

    return "unknown code"


# ---------- IP change ----------

def note_ip(*, browser, ip, session):
    """A changed address clears the trust window and this session's OTP. The user must verify again."""
    if ip is None or browser.last_ip == ip:
        return
    if browser.last_ip:
        session.pop("otp_ok", None)
        browser.trusted_until = None
        audit(AuditAction.IP_CHANGED, f"Address changed from {browser.last_ip} to {ip}.",
              actor=browser.user, ip=ip)
    browser.last_ip = ip
    browser.save(update_fields=["last_ip", "trusted_until"])


# ---------- OTP, backup codes and enrolment ----------

def otp_satisfied(*, browser, session):
    if session.get("otp_ok"):
        return True
    return bool(browser.trusted_until and browser.trusted_until > _now())


def is_enrolled(user):
    return OTPEnrolment.objects.filter(user=user).exists()


def _new_email_code():
    return f"{secrets.randbelow(10 ** 6):06d}"


def send_login_code(*, user, browser, ip=None):
    latest = EmailOTP.objects.filter(user=user, browser=browser, purpose=EmailOTP.LOGIN,
                                     used=False).order_by("-created_at").first()
    if latest and latest.created_at > _now() - OTP_RESEND_WAIT:
        return False
    code = _new_email_code()
    EmailOTP.objects.create(user=user, browser=browser, purpose=EmailOTP.LOGIN,
                            code_hash=_hash(code), expires_at=_now() + OTP_LIFETIME)
    _email(user, code)
    audit(AuditAction.OTP_SENT, "A sign-in code was emailed.", actor=user, ip=ip)
    return True


def _email(user, code):
    if not user.email:
        raise AccessError("Your account has no email address. Ask an administrator to add one.")
    send_mail("Your HCMS sign-in code",
              f"Your sign-in code is {code}. It expires in 2 minutes. Do not share it with anyone.",
              None, [user.email], fail_silently=False)


def _normalise_backup(code):
    return (code or "").replace("-", "").replace(" ", "").upper()


def _consume_backup_code(user, code):
    row = (BackupCode.objects.select_for_update()
           .filter(user=user, code_hash=_hash(_normalise_backup(code)), used_at__isnull=True).first())
    if row is None:
        return False
    row.used_at = _now()
    row.save(update_fields=["used_at"])
    return True


def issue_backup_codes(user, *, ip=None):
    """Replaces any existing set. Returns the new codes in display form. Shown once."""
    BackupCode.objects.filter(user=user, used_at__isnull=True).delete()
    codes = []
    for _ in range(BACKUP_CODE_COUNT):
        raw = "".join(secrets.choice(BACKUP_ALPHABET) for _ in range(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
        BackupCode.objects.create(user=user, code_hash=_hash(raw))
    audit(AuditAction.BACKUP_CODES_ISSUED, f"{BACKUP_CODE_COUNT} backup codes issued.", actor=user, ip=ip)
    return codes


def enrol_user(user, *, ip=None):
    """Called once, when the first email code is confirmed. Returns the backup codes to show."""
    OTPEnrolment.objects.get_or_create(user=user, defaults={"confirmed_at": _now()})
    audit(AuditAction.OTP_ENROLLED, "Email sign-in confirmed at first login.", actor=user, ip=ip)
    return issue_backup_codes(user, ip=ip)


def verify_login_code(*, browser, session, code, ip=None):
    """
    Accepts the emailed code, or one unused backup code. Three wrong codes lock the browser and alert admins.
    State changes are committed in a block BEFORE any error is raised, so failure counts and audit rows survive.
    """
    code = (code or "").strip()
    error = None
    locked_now = False
    with transaction.atomic():
        browser = AuthorisedBrowser.objects.select_for_update().get(pk=browser.pk)
        if browser.status == AuthorisedBrowser.LOCKED:
            error = "This browser is locked after too many wrong codes. Ask an administrator to unlock it."
        else:
            otp = (EmailOTP.objects.filter(browser=browser, purpose=EmailOTP.LOGIN, used=False)
                   .order_by("-created_at").first())
            email_ok = bool(otp and otp.expires_at > _now()
                            and hmac.compare_digest(otp.code_hash, _hash(code)))
            backup_ok = False if email_ok else _consume_backup_code(browser.user, code)
            if email_ok or backup_ok:
                if email_ok:
                    otp.used = True
                    otp.save(update_fields=["used"])
                browser.otp_failures = 0
                browser.trusted_until = _now() + TRUST_DURATION
                browser.save(update_fields=["otp_failures", "trusted_until"])
                if backup_ok:
                    audit(AuditAction.BACKUP_CODE_USED, "A backup code was used to sign in.",
                          actor=browser.user, ip=ip)
            else:
                browser.otp_failures += 1
                if browser.otp_failures >= OTP_MAX_FAILURES:
                    browser.status = AuthorisedBrowser.LOCKED
                    locked_now = True
                    error = "Too many wrong codes. This browser is locked until an administrator unlocks it."
                else:
                    error = (f"That code is wrong or has expired. "
                             f"{OTP_MAX_FAILURES - browser.otp_failures} attempt(s) left.")
                browser.save(update_fields=["otp_failures", "status"])
                audit(AuditAction.OTP_FAILED, f"A wrong sign-in code was entered ({browser.otp_failures}).",
                      actor=browser.user, ip=ip)
                if locked_now:
                    audit(AuditAction.BROWSER_LOCKED, f"A browser of {browser.user} was locked.",
                          actor=browser.user, ip=ip)
    if locked_now:
        alert_admins(f"{browser.user} was locked out of a browser after wrong sign-in codes (from {ip}).")
    if error:
        raise AccessError(error)
    session["otp_ok"] = True


# ---------- sign-in attempts (called from signals) ----------

def record_failed_password(*, username, ip):
    audit(AuditAction.LOGIN_FAILED, f"Failed sign-in for '{username}' from {ip}.", ip=ip)
    key = f"loginfail:{username}"
    cache.add(key, 0, timeout=LOGIN_FAILURE_WINDOW)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=LOGIN_FAILURE_WINDOW)
        count = 1
    if count == LOGIN_FAILURE_LIMIT:
        alert_admins(f"{LOGIN_FAILURE_LIMIT} failed sign-ins for '{username}' within 15 minutes (last from {ip}).",
                     action=AuditAction.ATTEMPTS_ALERT, ip=ip)


# ---------- emergency access ----------

def start_emergency(*, admin, ip=None):
    """Step two of the emergency page: emails the admin a code. Alerts every other administrator."""
    code = _new_email_code()
    EmailOTP.objects.filter(user=admin, purpose=EmailOTP.EMERGENCY, used=False).update(used=True)
    EmailOTP.objects.create(user=admin, purpose=EmailOTP.EMERGENCY, code_hash=_hash(code),
                            expires_at=_now() + OTP_LIFETIME)
    _email(admin, code)
    alert_admins(f"Emergency access was started by {admin} from {ip}.",
                 action=AuditAction.EMERGENCY_STARTED, ip=ip)


def check_emergency_code(*, admin, code, ip=None):
    otp = (EmailOTP.objects.filter(user=admin, purpose=EmailOTP.EMERGENCY, used=False)
           .order_by("-created_at").first())
    if otp is None or otp.expires_at <= _now() or not hmac.compare_digest(otp.code_hash, _hash((code or "").strip())):
        audit(AuditAction.EMERGENCY_REFUSED, "A wrong emergency code was entered.", actor=admin, ip=ip)
        raise AccessError("That code is wrong or has expired.")
    otp.used = True
    otp.save(update_fields=["used"])


def issue_emergency_pass(*, admin, ip=None):
    """Returns the pass code once. Each issue uses one of the three allowed per cycle."""
    exhausted = False
    with transaction.atomic():
        allowance, _ = EmergencyAllowance.objects.select_for_update().get_or_create(admin=admin)
        if allowance.uses_used >= EmergencyAllowance.MAX_PER_CYCLE:
            exhausted = True
        else:
            allowance.uses_used += 1
            allowance.save(update_fields=["uses_used", "updated_at"])
            code = secrets.token_urlsafe(12)
            EmergencyPass.objects.create(admin=admin, token_hash=_hash(code),
                                         expires_at=_now() + EMERGENCY_PASS_LIFETIME)
    if exhausted:
        audit(AuditAction.EMERGENCY_REFUSED, "Emergency allowance used up.", actor=admin, ip=ip)
        raise AccessError("Emergency access has been used up for this cycle. A developer must reset it.")
    alert_admins(f"An emergency pass was issued to {admin} ({allowance.uses_used} of 3 used this cycle).",
                 action=AuditAction.EMERGENCY_PASS_ISSUED, ip=ip)
    return code


def reset_emergency_allowances():
    return EmergencyAllowance.objects.update(uses_used=0)
