"""Radiology rules: a scan is performed only once paid, and an unpaid scan can be cancelled."""
from decimal import Decimal

from django.test import TestCase

from billing import services as bill
from billing.payments import take_payment
from hospital_system.testing import make_encounter, make_patient, make_staff
from radiology import services as rad
from radiology.models import ScanOrder


class ScanPrepayTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.doctor = make_staff("DOCTOR")
        self.billing = make_staff("BILLING")
        self.patient = make_patient(self.reception)
        self.encounter = make_encounter(self.patient, self.reception)
        self.scan = ScanOrder.objects.create(encounter=self.encounter, ordered_by=self.doctor,
                                             modality="X-Ray", body_part="Chest",
                                             clinical_indication="Chest pain")
        bill.add_charge(encounter=self.encounter, source_type="SCAN_ORDER", description="X-Ray",
                        unit_price=Decimal("1500"), staff=self.doctor, source_id=self.scan.id,
                        key=f"scan:{self.scan.id}")

    def test_unpaid_scan_is_refused(self):
        with self.assertRaises(rad.ScanPrepayError):
            rad.check_scan_prepaid(self.scan)

    def test_scan_proceeds_once_paid(self):
        for c in bill.open_lines(self.encounter):
            bill.post_charge_for_release(c, self.billing)
        take_payment(encounter=self.encounter, method="MANUAL",
                     amount_raw=str(bill.encounter_tally(self.encounter)["outstanding"]),
                     reference="", payer_phone="", staff=self.billing)
        rad.check_scan_prepaid(self.scan)  # must not raise

    def test_unpaid_scan_can_be_cancelled(self):
        self.assertTrue(rad.scan_cancellable(self.scan))
