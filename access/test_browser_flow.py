"""
End-to-end: a real Chromium runs the browser script. Skipped when no Chromium can be launched.
Set HCMS_CHROMIUM to the browser binary if Playwright cannot find one.
"""
import os
import unittest
from unittest import mock

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

from hospital_system.testing import make_staff

from . import services
from .models import AuthorisedBrowser

PASSWORD = "x-test-password-123"
FIXED_CODE = "246810"

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


@override_settings(BROWSER_AUTHORISATION_ENFORCED=True)
class BrowserFlowE2E(StaticLiveServerTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if sync_playwright is None:
            raise unittest.SkipTest("Playwright is not installed.")
        # Playwright's sync API keeps an event loop running in this thread, and Django would otherwise
        # refuse database calls from it. The tests only touch the database from the test thread.
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        cls._pw = sync_playwright().start()
        exe = os.environ.get("HCMS_CHROMIUM") or None
        try:
            cls.browser = cls._pw.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        except Exception as exc:  # no browser on this machine
            cls._pw.stop()
            raise unittest.SkipTest(f"No Chromium available: {str(exc)[:120]}")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "browser"):
            cls.browser.close()
        if hasattr(cls, "_pw"):
            cls._pw.stop()
        os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        super().tearDownClass()

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.admin = make_staff("ADMIN")
        self.admin.user.email = "admin@example.org"
        self.admin.user.save(update_fields=["email"])
        pending = AuthorisedBrowser.objects.create(user=self.admin.user)
        self.code = services.approve_browser(browser=pending, admin=self.admin.user, bootstrap=True)

    def test_approve_key_login_and_copied_cookie_is_refused(self):
        base = self.live_server_url
        with mock.patch("access.services._new_email_code", return_value=FIXED_CODE):
            context = self.browser.new_context()
            page = context.new_page()
            page.set_default_timeout(20000)

            # 1. Redeem the approval code. The browser creates its key and sends only the public part.
            page.goto(base + "/access/redeem/")
            page.fill("input[name=code]", self.code)
            page.click("#redeem-form button[type=submit]")
            page.wait_for_selector("input[name=username]")

            # 2. Sign in. First login asks for the emailed code.
            page.fill("input[name=username]", self.admin.user.username)
            page.fill("input[name=password]", PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_url("**/access/otp/**")
            page.fill("input[name=code]", FIXED_CODE)
            page.click("button[type=submit]")

            # 3. First-login enrolment shows backup codes once.
            page.wait_for_url("**/access/backup-codes/show/**")
            self.assertEqual(page.locator(".codes div").count(), services.BACKUP_CODE_COUNT)
            page.click("a.btn")

            # 4. The approved browser reaches the site.
            page.goto(base + "/patients/")
            self.assertNotIn("could not be verified", page.content().lower())
            self.assertNotIn("/access/", page.url)

            # 5. Copy the browser cookie into a fresh browser that has no key. It must not get in.
            cookies = [c for c in context.cookies() if c["name"] == services.COOKIE_NAME]
            self.assertTrue(cookies)
            other = self.browser.new_context()
            other.add_cookies([{"name": services.COOKIE_NAME, "value": cookies[0]["value"],
                                "url": base}])
            page2 = other.new_page()
            page2.set_default_timeout(20000)
            page2.goto(base + "/patients/")
            page2.wait_for_selector("text=could not be verified", timeout=20000)
            self.assertEqual(page2.locator("table").count(), 0)
            other.close()
            context.close()
