from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core_patients.models import StaffProfile
from pharmacy.models import StockBatch, StockItem, StockUnit


class Command(BaseCommand):
    help = "Create one opening unit for each medicine that has stock but no serial records."

    def add_arguments(self, parser):
        parser.add_argument("--expiry", required=True, help="Expiry date of the opening stock, YYYY-MM-DD.")
        parser.add_argument("--actor", required=True, help="Username of the staff member recording it.")

    @transaction.atomic
    def handle(self, *args, **opts):
        try:
            expiry = date.fromisoformat(opts["expiry"])
        except ValueError:
            raise CommandError("Use --expiry YYYY-MM-DD.")
        staff = StaffProfile.objects.filter(user__username=opts["actor"]).first()
        if staff is None:
            raise CommandError("No staff profile for that username.")

        created = 0
        for stock in StockItem.objects.select_related("medication"):
            if stock.quantity <= 0 or StockUnit.objects.filter(medication=stock.medication).exists():
                continue
            batch = StockBatch.objects.create(
                medication=stock.medication, batch_number="OPENING", expiry_date=expiry, received_by=staff)
            StockUnit.objects.create(
                serial=f"OPEN-{str(stock.medication_id)[:8].upper()}", medication=stock.medication,
                batch=batch, doses_total=stock.quantity, doses_remaining=stock.quantity)
            created += 1
        self.stdout.write(self.style.SUCCESS(f"Created {created} opening unit(s)."))
