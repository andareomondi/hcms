from django.core.management.base import BaseCommand

from core_patients.models import StaffProfile
from pharmacy.services import expire_units


class Command(BaseCommand):
    help = "Write off every pack or vial past its expiry date."

    def add_arguments(self, parser):
        parser.add_argument("--actor", required=True,
                            help="Username of the staff member the write-offs are recorded against.")

    def handle(self, *args, **opts):
        staff = StaffProfile.objects.filter(user__username=opts["actor"]).first()
        if staff is None:
            raise SystemExit("No staff profile for that username.")
        expired = expire_units(staff=staff)
        self.stdout.write(f"Expired {len(expired)} unit(s).")
