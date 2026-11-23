from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from access import services
from access.models import AuthorisedBrowser
from hospital_system.testing import make_staff


class EmergencyBrowserResetTests(TestCase):
    def setUp(self):
        self.admin = make_staff("ADMIN", username="boss")
        self.other_admin = make_staff("ADMIN", username="deputy")
        self.old = AuthorisedBrowser.objects.create(
            user=self.admin.user, status=AuthorisedBrowser.ACTIVE, user_agent="old", token_hash="x" * 64)

    def _run(self, username, typed):
        out = StringIO()
        with mock.patch("builtins.input", return_value=typed):
            call_command("emergency_browser_reset", username=username, stdout=out)
        return out.getvalue()

    def test_revokes_old_browsers_and_prints_a_code_that_redeems(self):
        output = self._run("boss", "boss")
        self.old.refresh_from_db()
        self.assertEqual(self.old.status, AuthorisedBrowser.REVOKED)
        new = AuthorisedBrowser.objects.get(user=self.admin.user, status=AuthorisedBrowser.AWAITING_CODE)
        code = [line.strip() for line in output.splitlines() if line.strip()][-2]
        self.assertTrue(new.redeem_hash)
        self.assertEqual(new.redeem_hash, services._hash(code))

    def test_wrong_confirmation_changes_nothing(self):
        with self.assertRaises(CommandError):
            self._run("boss", "BOSS")
        self.old.refresh_from_db()
        self.assertEqual(self.old.status, AuthorisedBrowser.ACTIVE)
        self.assertFalse(AuthorisedBrowser.objects.filter(status=AuthorisedBrowser.AWAITING_CODE).exists())

    def test_refuses_a_non_admin(self):
        make_staff("NURSE", username="nurse1")
        with self.assertRaises(CommandError):
            self._run("nurse1", "nurse1")

    def test_refuses_unknown_user(self):
        with self.assertRaises(CommandError):
            self._run("nobody", "nobody")
