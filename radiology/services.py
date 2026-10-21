from billing import services as billing
from billing.models import Charge


class ScanPrepayError(Exception):
    """A scan is not paid for. Shown as-is."""


def scan_charge_or_none(scan):
    return (Charge.objects.filter(idempotency_key=f"scan:{scan.id}")
            .exclude(status="VOIDED").first())


def scan_prepaid(scan):
    if scan.encounter.prepay_bypassed or scan.credit_approved_by_id:
        return True
    charge = scan_charge_or_none(scan)
    return charge is not None and billing.charge_fully_paid(charge)


def check_scan_prepaid(scan):
    if not scan_prepaid(scan):
        raise ScanPrepayError("Awaiting payment. The patient must pay for this scan at billing "
                              "before the report can be submitted.")
