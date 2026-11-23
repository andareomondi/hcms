"""
Emergency recovery for an administrator who has lost every authorised browser.

Run on the server shell by the developer. It revokes that administrator's browser records, issues a
new random one-time code for one new browser, and alerts every other administrator. The code is shown
once, expires with the normal redeem lifetime and works once. Nothing is hardcoded.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from access import services
from access.models import AuthorisedBrowser
from core_patients.models import StaffProfile
from dashboard.audit_actions import AuditAction


class Command(BaseCommand):
    help = ("Emergency: revokes all browsers of one administrator and prints a one-time code for a new browser. "
            "Run on the server shell. Every other administrator is alerted.")

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="The administrator who is locked out.")

    def handle(self, *args, **options):
        username = options["username"].strip()
        user = get_user_model().objects.filter(username=username).first()
        if user is None or not user.is_active:
            raise CommandError("No active user with that username.")
        if not StaffProfile.objects.filter(user=user, role="ADMIN", is_active=True).exists():
            raise CommandError("That user is not an active administrator.")

        self.stdout.write(f"This revokes every browser of '{username}' and issues one new code.")
        typed = input(f"Type the username '{username}' to confirm: ").strip()
        if typed != username:
            raise CommandError("The username did not match. Nothing was changed.")

        with transaction.atomic():
            revoked = 0
            for browser in AuthorisedBrowser.objects.filter(user=user).exclude(status=AuthorisedBrowser.REVOKED):
                services.revoke_browser(browser=browser, admin=user)
                revoked += 1
            browser = AuthorisedBrowser.objects.create(
                user=user, status=AuthorisedBrowser.PENDING, user_agent="emergency reset command")
            code = services.approve_browser(browser=browser, admin=user, bootstrap=True)
            services.alert_admins(
                f"Emergency browser reset for {username}: {revoked} browser(s) revoked, a new code was issued.",
                action=AuditAction.EMERGENCY_BROWSER_RESET)

        self.stdout.write(self.style.SUCCESS(
            f"Revoked {revoked} browser(s) of {username}.\n"
            f"One-time code, valid {services.REDEEM_LIFETIME}, works once:\n{code}\n"
            "Open /access/redeem/ on the address you use and enter it."))
