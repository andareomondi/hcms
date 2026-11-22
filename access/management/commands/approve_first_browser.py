from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from access import services
from access.models import AuthorisedBrowser
from core_patients.models import StaffProfile


class Command(BaseCommand):
    help = ("Bootstrap: issues a one-time code for the first browser of an administrator. "
            "Refused once any browser is already active. Run on the server by the developer.")

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="The administrator's username.")

    def handle(self, *args, **options):
        if AuthorisedBrowser.objects.filter(status=AuthorisedBrowser.ACTIVE).exists():
            raise CommandError("An approved browser already exists. Use the browser admin screen instead.")
        user = get_user_model().objects.filter(username=options["username"]).first()
        if user is None or not user.is_active:
            raise CommandError("No active user with that username.")
        if not StaffProfile.objects.filter(user=user, role="ADMIN", is_active=True).exists():
            raise CommandError("That user is not an active administrator.")
        browser = AuthorisedBrowser.objects.create(user=user, status=AuthorisedBrowser.PENDING,
                                                   user_agent="bootstrap command")
        code = services.approve_browser(browser=browser, admin=user, bootstrap=True)
        self.stdout.write(self.style.SUCCESS(
            f"One-time code for {user.username}, valid 30 minutes, works once:\n{code}\n"
            "Open /access/redeem/ in the browser to authorise and enter it."))
