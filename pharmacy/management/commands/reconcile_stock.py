from django.core.management.base import BaseCommand
from django.db.models import Sum

from pharmacy.models import StockItem, StockUnit


class Command(BaseCommand):
    help = "Check that each medicine's stock total equals the doses left on its serials."

    def handle(self, *args, **opts):
        problems = 0
        for stock in StockItem.objects.select_related("medication"):
            on_serials = (StockUnit.objects.filter(medication=stock.medication, status="IN_STOCK")
                          .aggregate(t=Sum("doses_remaining"))["t"] or 0)
            if on_serials != stock.quantity:
                problems += 1
                self.stdout.write(self.style.ERROR(
                    f"{stock.medication.brand_name} {stock.medication.strength}: "
                    f"stock says {stock.quantity}, serials say {on_serials}"))
        if problems:
            raise SystemExit(f"{problems} medicine(s) out of step. Investigate before dispensing more.")
        self.stdout.write(self.style.SUCCESS("Stock and serials agree for every medicine."))
