"""Browser authorisation, browser key, OTP, backup codes, audit, alerts and emergency access."""
import base64
import secrets
from datetime import timedelta
from io import StringIO
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core_patients.models import StaffProfile
from dashboard.models import AuditLog
from hospital_system.testing import make_staff
from notifications.models import Notification

from . import crypto, services
from .models import AuthorisedBrowser, BackupCode, EmergencyPass, OTPEnrolment

PASSWORD = "x-test-password-123"
FIXED_CODE = "246810"


def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _new_key():
    """Acts as the browser: returns (private key, public key JWK text the browser would send)."""
    private = ec.generate_private_key(ec.SECP256R1())
    numbers = private.public_key().public_numbers()
    jwk = {"kty": "EC", "crv": "P-256",
           "x": _b64(numbers.x.to_bytes(32, "big")), "y": _b64(numbers.y.to_bytes(32, "big"))}
    import json
    return private, json.dumps(jwk)


def _with_email(profile):
    profile.user.email = f"{profile.user.username}@example.org"
    profile.user.save(update_fields=["email"])
    return profile


def _authorise(client, user, *, trusted=True, last_ip="127.0.0.1"):
    """An approved browser for this user. Returns (browser, private key). Gives the client its cookie."""
    private, jwk = _new_key()
    token = secrets.token_urlsafe(32)
    browser = AuthorisedBrowser.objects.create(
        user=user, status=AuthorisedBrowser.ACTIVE, token_hash=services._hash(token),
        public_key_jwk=jwk, expires_at=timezone.now() + timedelta(days=90), last_ip=last_ip,
        trusted_until=timezone.now() + timedelta(hours=12) if trusted else None)
    client.cookies[services.COOKIE_NAME] = token
    return browser, private


def _prove(client, private):
    """Runs the browser's check: fetch a challenge, sign it, post the signature. The proof cookie is stored."""
    nonce = client.get("/access/challenge/").content.decode()
    signature = crypto.sign_raw(private, nonce.encode())
    response = client.post("/access/proof/", {"nonce": nonce, "signature": signature})
    assert response.status_code == 200, response.status_code
    return response


def _admin_profile_ids():
    return list(StaffProfile.objects.filter(role="ADMIN").values_list("id", flat=True))


@override_settings(BROWSER_AUTHORISATION_ENFORCED=True)
class BaseAccessTest(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = _with_email(make_staff("RECEPTION"))
        self.admin = _with_email(make_staff("ADMIN"))


class BrowserGateTests(BaseAccessTest):
    def test_unknown_browser_gets_bare_403(self):
        response = Client().get("/patients/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content, b"")

    def test_login_page_is_refused_for_unknown_browser(self):
        self.assertEqual(Client().get("/accounts/login/").status_code, 403)

    def test_public_enrolment_page_is_reachable(self):
        self.assertEqual(Client().get("/access/enrol/").status_code, 200)

    def test_copied_cookie_without_proof_gets_check_page_not_content(self):
        client = Client()
        _authorise(client, self.staff.user)
        response = client.get("/patients/")
        self.assertEqual(response.status_code, 403)
        self.assertIn(b"hcms_proof.js", response.content)
        self.assertNotIn(b"Patient", response.content)

    def test_copied_cookie_cannot_get_a_proof_without_the_key(self):
        client = Client()
        _browser, _real_private = _authorise(client, self.staff.user)
        _other_private, _ = _new_key()  # the attacker's own key
        nonce = client.get("/access/challenge/").content.decode()
        signature = crypto.sign_raw(_other_private, nonce.encode())
        response = client.post("/access/proof/", {"nonce": nonce, "signature": signature})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(AuditLog.objects.filter(action_type="PROOF_FAILED").exists())

    def test_challenge_cannot_be_replayed(self):
        client = Client()
        _browser, private = _authorise(client, self.staff.user)
        nonce = client.get("/access/challenge/").content.decode()
        signature = crypto.sign_raw(private, nonce.encode())
        self.assertEqual(client.post("/access/proof/", {"nonce": nonce, "signature": signature}).status_code, 200)
        self.assertEqual(client.post("/access/proof/", {"nonce": nonce, "signature": signature}).status_code, 403)

    def test_forged_challenge_is_refused(self):
        client = Client()
        _browser, private = _authorise(client, self.staff.user)
        nonce = client.get("/access/challenge/").content.decode()
        forged = nonce.rsplit(".", 1)[0] + ".deadbeef"
        signature = crypto.sign_raw(private, forged.encode())
        self.assertEqual(client.post("/access/proof/", {"nonce": forged, "signature": signature}).status_code, 403)

    def test_browser_with_proof_and_otp_trust_reaches_the_site(self):
        client = Client()
        _browser, private = _authorise(client, self.staff.user)
        _prove(client, private)
        client.force_login(self.staff.user)
        self.assertEqual(client.get("/patients/").status_code, 200)


class ApprovalTests(BaseAccessTest):
    def setUp(self):
        super().setUp()
        self.admin_client = Client()
        _browser, admin_private = _authorise(self.admin_client, self.admin.user)
        _prove(self.admin_client, admin_private)
        self.admin_client.force_login(self.admin.user)
        self.pending = AuthorisedBrowser.objects.create(user=self.staff.user, enrolled_ip="10.0.0.5")

    def test_admin_approves_then_user_redeems_with_a_key(self):
        response = self.admin_client.post(f"/access/browsers/{self.pending.pk}/action/", {"action": "approve"})
        self.assertEqual(response.status_code, 200)
        code = response.context["issued_code"]
        _private, jwk = _new_key()
        user_client = Client()
        redeem = user_client.post("/access/redeem/", {"code": code, "public_key": jwk})
        self.assertEqual(redeem.status_code, 302)
        self.assertIn(services.COOKIE_NAME, redeem.cookies)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, AuthorisedBrowser.ACTIVE)
        self.assertTrue(self.pending.public_key_jwk)
        self.assertEqual(Client().post("/access/redeem/", {"code": code, "public_key": jwk}).status_code, 200)

    def test_redeem_is_refused_without_a_valid_key(self):
        code = services.approve_browser(browser=self.pending, admin=self.admin.user)
        with self.assertRaises(services.AccessError):
            services.redeem_code(code=code, public_key_jwk="")
        with self.assertRaises(services.AccessError):
            services.redeem_code(code=code, public_key_jwk='{"kty":"EC","crv":"P-256","x":"AA","y":"AA"}')

    def test_expired_code_is_refused(self):
        code = services.approve_browser(browser=self.pending, admin=self.admin.user)
        self.pending.refresh_from_db()
        self.pending.redeem_expires_at = timezone.now() - timedelta(minutes=1)
        self.pending.save()
        _private, jwk = _new_key()
        with self.assertRaises(services.AccessError):
            services.redeem_code(code=code, public_key_jwk=jwk)

    def test_approval_revoke_and_unlock_are_audited(self):
        services.approve_browser(browser=self.pending, admin=self.admin.user)
        browser, _ = _authorise(Client(), self.staff.user)
        services.revoke_browser(browser=browser, admin=self.admin.user)
        for action in ("BROWSER_APPROVED", "BROWSER_REVOKED"):
            self.assertTrue(AuditLog.objects.filter(action_type=action).exists(), action)

    def test_admin_cannot_approve_own_browser(self):
        own = AuthorisedBrowser.objects.create(user=self.admin.user)
        with self.assertRaises(services.AccessError):
            services.approve_browser(browser=own, admin=self.admin.user)

    def test_non_admin_cannot_open_the_browser_screen(self):
        client = Client()
        _browser, private = _authorise(client, self.staff.user)
        _prove(client, private)
        client.force_login(self.staff.user)
        self.assertEqual(client.get("/access/browsers/").status_code, 403)


class LoginOtpTests(BaseAccessTest):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.browser, self.private = _authorise(self.client, self.staff.user, trusted=False)
        _prove(self.client, self.private)

    def _login(self, username=None, password=PASSWORD):
        return self.client.post("/accounts/login/", {"username": username or self.staff.user.username,
                                                     "password": password})

    def _send_code(self):
        with mock.patch("access.services._new_email_code", return_value=FIXED_CODE):
            self.client.get("/access/otp/?next=/patients/")

    def test_first_login_confirms_email_and_shows_backup_codes(self):
        self._login()
        self._send_code()
        self.assertEqual(len(mail.outbox), 1)
        response = self.client.post("/access/otp/?next=/patients/", {"code": FIXED_CODE, "next": "/patients/"})
        self.assertRedirects(response, "/access/backup-codes/show/", fetch_redirect_response=False)
        self.assertTrue(OTPEnrolment.objects.filter(user=self.staff.user).exists())
        shown = self.client.get("/access/backup-codes/show/")
        self.assertEqual(len(shown.context["codes"]), services.BACKUP_CODE_COUNT)
        self.assertEqual(self.client.get("/access/backup-codes/show/").status_code, 302)  # shown once

    def test_three_wrong_codes_lock_the_browser_and_alert_admins(self):
        self._login()
        self._send_code()
        for _ in range(3):
            self.client.post("/access/otp/?next=/patients/", {"code": "000000", "next": "/patients/"})
        self.browser.refresh_from_db()
        self.assertEqual(self.browser.status, AuthorisedBrowser.LOCKED)
        self.assertTrue(AuditLog.objects.filter(action_type="BROWSER_LOCKED").exists())
        self.assertTrue(Notification.objects.filter(recipient__in=_admin_profile_ids(),
                                                    kind="SECURITY_ALERT").exists())

    def test_wrong_codes_are_audited(self):
        self._login()
        self._send_code()
        self.client.post("/access/otp/?next=/patients/", {"code": "000000", "next": "/patients/"})
        self.assertTrue(AuditLog.objects.filter(action_type="OTP_FAILED").exists())

    def test_backup_code_works_once_and_is_audited(self):
        services.enrol_user(self.staff.user)
        codes = services.issue_backup_codes(self.staff.user)
        self._login()
        response = self.client.post("/access/otp/?next=/patients/", {"code": codes[0], "next": "/patients/"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action_type="BACKUP_CODE_USED").exists())
        self.assertEqual(BackupCode.objects.filter(used_at__isnull=True).count(), len(codes) - 1)
        self.client.post("/accounts/logout/")
        self._login()
        self.browser.trusted_until = None
        self.browser.save()
        again = self.client.post("/access/otp/?next=/patients/", {"code": codes[0], "next": "/patients/"})
        self.assertEqual(again.status_code, 200)  # the same code does not work twice

    def test_regenerating_backup_codes_invalidates_old_ones(self):
        services.enrol_user(self.staff.user)
        old = services.issue_backup_codes(self.staff.user)
        services.issue_backup_codes(self.staff.user)
        self.assertFalse(BackupCode.objects.filter(
            code_hash=services._hash(old[0].replace("-", ""))).exclude(used_at__isnull=False).exists())

    def test_trusted_browser_skips_otp_for_twelve_hours(self):
        self.browser.trusted_until = timezone.now() + timedelta(hours=12)
        self.browser.save()
        self._login()
        self.assertEqual(self.client.get("/patients/").status_code, 200)

    def test_new_ip_requires_otp_again_and_is_audited(self):
        self.browser.trusted_until = timezone.now() + timedelta(hours=12)
        self.browser.save()
        self._login()
        self.assertEqual(self.client.get("/patients/").status_code, 200)
        response = self.client.get("/patients/", REMOTE_ADDR="203.0.113.9")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/access/otp/", response["Location"])
        self.assertTrue(AuditLog.objects.filter(action_type="IP_CHANGED").exists())

    def test_second_staff_login_in_the_same_browser_is_refused_and_audited(self):
        other = _with_email(make_staff("DOCTOR"))
        self.browser.trusted_until = timezone.now() + timedelta(hours=12)
        self.browser.save()
        self._login()
        response = self.client.post("/accounts/login/", {"username": other.user.username, "password": PASSWORD})
        self.assertEqual(response.status_code, 200)  # the form is shown again, with the reason
        self.assertTrue(AuditLog.objects.filter(action_type="LOGIN_REFUSED").exists())
        self.assertEqual(self.client.session.get("_auth_user_id"), str(self.staff.user.pk))

    def test_logging_out_does_not_revoke_the_browser(self):
        self.browser.trusted_until = timezone.now() + timedelta(hours=12)
        self.browser.save()
        self._login()
        self.client.post("/accounts/logout/")
        self.browser.refresh_from_db()
        self.assertEqual(self.browser.status, AuthorisedBrowser.ACTIVE)
        self.assertTrue(AuditLog.objects.filter(action_type="LOGOUT").exists())

    def test_login_success_is_audited(self):
        self.browser.trusted_until = timezone.now() + timedelta(hours=12)
        self.browser.save()
        self._login()
        self.assertTrue(AuditLog.objects.filter(action_type="LOGIN_SUCCEEDED").exists())

    def test_repeated_wrong_passwords_alert_admins(self):
        for _ in range(services.LOGIN_FAILURE_LIMIT):
            self._login(password="wrong-password")
        self.assertTrue(AuditLog.objects.filter(action_type="LOGIN_FAILED").exists())
        self.assertTrue(Notification.objects.filter(recipient__in=_admin_profile_ids(),
                                                    kind="SECURITY_ALERT").exists())


class EmergencyTests(BaseAccessTest):
    def _run_once(self, client):
        with mock.patch("access.services._new_email_code", return_value=FIXED_CODE):
            client.post("/access/emergency/", {"username": self.admin.user.username, "password": PASSWORD})
        return client.post("/access/emergency/", {"code": FIXED_CODE})

    def test_three_passes_per_cycle_then_refused(self):
        for _ in range(3):
            self.assertEqual(self._run_once(Client()).context["step"], "issued")
        self.assertNotEqual(self._run_once(Client()).context.get("step"), "issued")

    def test_reset_command_restores_the_allowance(self):
        for _ in range(3):
            self._run_once(Client())
        call_command("reset_emergency_allowance", stdout=StringIO())
        self.assertEqual(self._run_once(Client()).context["step"], "issued")

    def test_emergency_start_and_pass_are_audited_and_alerted(self):
        self._run_once(Client())
        self.assertTrue(AuditLog.objects.filter(action_type="EMERGENCY_STARTED").exists())
        self.assertTrue(AuditLog.objects.filter(action_type="EMERGENCY_PASS_ISSUED").exists())
        self.assertTrue(Notification.objects.filter(recipient__in=_admin_profile_ids(),
                                                    kind="SECURITY_ALERT").exists())

    def test_pass_works_once_on_a_key_bound_browser(self):
        code = services.issue_emergency_pass(admin=self.admin.user)
        _private, jwk = _new_key()
        _token, browser = services.redeem_code(code=code, public_key_jwk=jwk)
        self.assertTrue(browser.is_emergency)
        self.assertTrue(AuditLog.objects.filter(action_type="EMERGENCY_PASS_USED").exists())
        with self.assertRaises(services.AccessError):
            services.redeem_code(code=code, public_key_jwk=jwk)

    def test_expired_pass_is_refused(self):
        code = services.issue_emergency_pass(admin=self.admin.user)
        EmergencyPass.objects.filter(token_hash=services._hash(code)).update(
            expires_at=timezone.now() - timedelta(minutes=1))
        _private, jwk = _new_key()
        with self.assertRaises(services.AccessError):
            services.redeem_code(code=code, public_key_jwk=jwk)

    def test_non_admin_cannot_start_emergency(self):
        staff = _with_email(make_staff("RECEPTION"))
        client = Client()
        response = client.post("/access/emergency/", {"username": staff.user.username, "password": PASSWORD})
        self.assertEqual(response.context["step"], "password")
        self.assertEqual(len(mail.outbox), 0)


class BootstrapCommandTests(BaseAccessTest):
    def test_first_browser_code_is_issued_and_works_once(self):
        out = StringIO()
        call_command("approve_first_browser", username=self.admin.user.username, stdout=out)
        code = out.getvalue().strip().splitlines()[1]
        _private, jwk = _new_key()
        _token, browser = services.redeem_code(code=code, public_key_jwk=jwk)
        self.assertEqual(browser.user_id, self.admin.user.id)
        with self.assertRaises(services.AccessError):
            services.redeem_code(code=code, public_key_jwk=jwk)

    def test_refused_once_an_active_browser_exists(self):
        _authorise(Client(), self.admin.user)
        with self.assertRaises(CommandError):
            call_command("approve_first_browser", username=self.admin.user.username, stdout=StringIO())

    def test_refused_for_non_admin(self):
        with self.assertRaises(CommandError):
            call_command("approve_first_browser", username=self.staff.user.username, stdout=StringIO())


class KeyValidationTests(TestCase):
    def test_key_must_be_p256_on_curve(self):
        import json
        with self.assertRaises(crypto.KeyError_):
            crypto.normalise_public_key(json.dumps({"kty": "RSA", "crv": "P-256", "x": "A", "y": "A"}))
        with self.assertRaises(crypto.KeyError_):
            crypto.normalise_public_key("not json")
        _private, jwk = _new_key()
        self.assertIn('"crv":"P-256"', crypto.normalise_public_key(jwk))
