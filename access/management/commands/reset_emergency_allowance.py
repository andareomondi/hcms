from django.core.management.base import BaseCommand

from access.services import reset_emergency_allowances


class Command(BaseCommand):
    help = "Resets the emergency access allowance (3 passes per cycle) for every admin. Run by a developer."

    def handle(self, *args, **options):
        count = reset_emergency_allowances()
        self.stdout.write(self.style.SUCCESS(f"Emergency allowance reset for {count} admin account(s)."))
