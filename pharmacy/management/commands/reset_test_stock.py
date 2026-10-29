"""
TEST ENVIRONMENTS ONLY.

Writes off expired stock, then restocks medicines with a batch that expires in 14 days,
so dispensing and administration can be tested end to end.

Refused unless DEBUG is on and --confirm is given. Never run this against a live database.

Usage:
    python manage.py reset_test_stock --actor <superuser-or-staff-username> --confirm
    python manage.py reset_test_stock --actor <username> --confirm --packs 20 --all
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core_patients.models import StaffProfile
from pharmacy import services
from pharmacy.models import Medication
from pharmacy.services import PharmacyError

TEST_EXPIRY_DAYS = 14


class Command(BaseCommand):
    help = "TEST ONLY: write off expired stock, then restock with a 14-day expiry."

    def add_arguments(self, parser):
        parser.add_argument("--actor", required=True, help="Username recorded on every movement.")
        parser.add_argument("--packs", type=int, default=10, help="Packs or vials per medicine.")
        parser.add_argument("--all", action="store_true",
                            help="Restock every active medicine, not only those with no usable stock.")
        parser.add_argument("--confirm", action="store_true", help="Required. Confirms a test environment.")

    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("Refused: DEBUG is off. This command is for test environments only.")
        if not opts["confirm"]:
            raise CommandError("Add --confirm to run this on the test database.")
        if opts["packs"] < 1:
            raise CommandError("--packs must be at least 1.")

        staff = StaffProfile.objects.filter(user__username=opts["actor"]).first()
        if staff is None:
            raise CommandError("No staff profile for that username.")

        today = timezone.localdate()
        expired = services.expire_due_units(staff=staff, today=today)
        self.stdout.write(f"Written off {len(expired)} expired unit(s).")

        # Unique per run, so re-running never collides with an earlier batch or serial.
        run = timezone.now().strftime("%m%d%H%M%S")
        expiry = today + timedelta(days=TEST_EXPIRY_DAYS)
        restocked, skipped, failed = 0, 0, []

        for med in Medication.objects.filter(is_active=True).order_by("brand_name"):
            if not opts["all"] and services.usable_doses(med, today) > 0:
                skipped += 1
                continue
            serials = [f"T{run}-{med.id.hex[:6].upper()}-{i:02d}" for i in range(1, opts["packs"] + 1)]
            try:
                services.receive_batch(
                    medication=med, batch_number=f"TEST{run}", expiry_date=expiry,
                    serials=serials, doses_per_unit=None, staff=staff,
                )
                restocked += 1
            except PharmacyError as exc:
                failed.append(f"{med.brand_name}: {exc}")

        self.stdout.write(self.style.SUCCESS(
            f"Restocked {restocked} medicine(s) with expiry {expiry:%d %b %Y}. "
            f"Already in stock and skipped: {skipped}."
        ))
        for line in failed:
            self.stdout.write(self.style.ERROR(f"Not restocked: {line}"))
