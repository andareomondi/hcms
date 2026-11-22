"""
Pharmacy rules: medicines are released only once paid (or billed to a ward stay),
stock is never oversold, serials must match the medicine, and expired or
out-of-scope items are refused.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from billing import services as bill
from billing.payments import take_payment
from hospital_system.testing import make_encounter, make_patient, make_staff
from pharmacy import services as ph
from pharmacy.models import Medication


def _medication(**kw):
    defaults = dict(brand_name="Amoxil", generic_name="Amoxicillin", form="Capsule",
                    strength="500mg", unit_price=Decimal("50.00"), doses_per_unit=10)
    defaults.update(kw)
    return Medication.objects.create(**defaults)


def _future():
    return timezone.localdate() + timedelta(days=365)


class PharmacyBase(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.doctor = make_staff("DOCTOR")
        self.pharmacist = make_staff("PHARMACIST")
        self.billing = make_staff("BILLING")
        self.patient = make_patient(self.reception)
        self.encounter = make_encounter(self.patient, self.reception)
        self.med = _medication()

    def stock(self, serials=("SN-1", "SN-2"), per_unit=10, med=None):
        return ph.receive_batch(medication=med or self.med, batch_number="B-100",
                                expiry_date=_future(), serials=list(serials),
                                doses_per_unit=per_unit, staff=self.pharmacist)

    def prescribe(self, doses=2, route="ORAL", encounter=None):
        return ph.create_formulary_order(
            encounter=encounter or self.encounter, medication=self.med, doctor=self.doctor,
            route=route, frequency="TDS", dose_text="1 capsule", days=5, doses_total=doses,
            override="", staff=self.doctor)


class StockReceivingTests(PharmacyBase):
    def test_batch_without_a_number_is_refused(self):
        with self.assertRaises(ph.PharmacyError):
            ph.receive_batch(medication=self.med, batch_number="  ", expiry_date=_future(),
                             serials=["SN-9"], doses_per_unit=10, staff=self.pharmacist)

    def test_expired_batch_is_refused(self):
        with self.assertRaises(ph.PharmacyError):
            ph.receive_batch(medication=self.med, batch_number="OLD", expiry_date=timezone.localdate(),
                             serials=["SN-9"], doses_per_unit=10, staff=self.pharmacist)

    def test_same_batch_number_cannot_be_received_twice(self):
        self.stock(serials=("SN-1",))
        with self.assertRaises(ph.PharmacyError):
            self.stock(serials=("SN-2",))

    def test_serial_already_in_system_is_refused(self):
        self.stock(serials=("SN-1",))
        with self.assertRaises(ph.PharmacyError):
            ph.receive_batch(medication=_medication(brand_name="Other"), batch_number="B-200",
                             expiry_date=_future(), serials=["SN-1"], doses_per_unit=10,
                             staff=self.pharmacist)


class PrescribingTests(PharmacyBase):
    def test_cannot_prescribe_more_doses_than_stock_holds(self):
        self.stock(serials=("SN-1",), per_unit=10)
        with self.assertRaises(ph.StockShortError):
            self.prescribe(doses=11)

    def test_prescription_within_stock_creates_a_provisional_charge(self):
        self.stock(serials=("SN-1",), per_unit=10)
        self.prescribe(doses=2)
        charge = bill.medication_charge_or_none(
            self.encounter.medication_orders.get())
        self.assertIsNotNone(charge)
        self.assertEqual(charge.status, "PROVISIONAL")
        self.assertEqual(charge.amount, Decimal("100.00"))


class PrepayGateTests(PharmacyBase):
    def setUp(self):
        super().setUp()
        self.stock(serials=("SN-1",), per_unit=10)
        self.order = self.prescribe(doses=2).__class__ and self.encounter.medication_orders.get()

    def test_unpaid_medicine_is_refused_at_pharmacy(self):
        with self.assertRaises(ph.PrepayError):
            ph.check_prepaid(self.order)

    def test_medicine_is_released_once_its_line_is_paid(self):
        charge = bill.medication_charge_or_none(self.order)
        bill.post_charge_for_release(charge, self.billing)
        take_payment(encounter=self.encounter, method="MANUAL", amount_raw="100",
                     reference="", payer_phone="", staff=self.billing)
        ph.check_prepaid(self.order)  # must not raise

    def test_partial_payment_does_not_release_medicine(self):
        charge = bill.medication_charge_or_none(self.order)
        bill.post_charge_for_release(charge, self.billing)
        take_payment(encounter=self.encounter, method="MANUAL", amount_raw="40",
                     reference="", payer_phone="", staff=self.billing)
        with self.assertRaises(ph.PrepayError):
            ph.check_prepaid(self.order)

    def test_emergency_bypass_releases_medicine_without_payment(self):
        self.encounter.prepay_bypassed = True
        self.encounter.save()
        ph.check_prepaid(self.order)

    def test_ward_medicine_is_released_and_billed_to_the_stay(self):
        stay = make_encounter(self.patient, self.reception, encounter_type="WARD_STAY")
        order = ph.create_formulary_order(
            encounter=self.encounter, medication=self.med, doctor=self.doctor, route="ORAL",
            frequency="OD", dose_text="1", days=1, doses_total=1, override="",
            staff=self.doctor, billing_encounter=stay)
        order = self.encounter.medication_orders.order_by("-ordered_at").first()
        charge = bill.medication_charge_or_none(order)
        self.assertEqual(charge.encounter, stay)
        ph.check_prepaid(order)  # billed to the stay, settled at discharge


class DispensingRouteTests(PharmacyBase):
    def setUp(self):
        super().setUp()
        self.stock(serials=("SN-1",), per_unit=10)

    def test_injection_cannot_be_dispensed_by_pharmacy(self):
        self.prescribe(doses=1, route="IM")
        order = self.encounter.medication_orders.get()
        self.encounter.prepay_bypassed = True
        self.encounter.save()
        with self.assertRaises(ph.PharmacyError):
            ph.dispense(order_id=order.id, quantity=1, staff=self.pharmacist)

    def test_oral_order_cannot_be_administered_by_a_nurse(self):
        self.prescribe(doses=1, route="ORAL")
        order = self.encounter.medication_orders.get()
        nurse = make_staff("NURSE")
        with self.assertRaises(ph.PharmacyError):
            ph.administer(order_id=order.id, nurse=nurse, dose_given_text="1 capsule")


class WriteOffTests(PharmacyBase):
    def test_write_off_needs_a_reason(self):
        self.stock(serials=("SN-1",))
        with self.assertRaises(ph.PharmacyError):
            ph.write_off(serial="SN-1", reason="", staff=self.pharmacist)

    def test_write_off_of_unknown_serial_is_refused(self):
        with self.assertRaises(ph.PharmacyError):
            ph.write_off(serial="NOPE", reason="Damaged", staff=self.pharmacist)


class MaternityMedicineTests(PharmacyBase):
    """Maternity medicines are paid after the stay and never block release."""

    def test_unpaid_maternity_medicine_is_released(self):
        from hospital_system.testing import make_encounter as _enc
        self.stock(serials=("SN-1",), per_unit=10)
        labour = _enc(self.patient, self.reception, encounter_type="MATERNITY")
        ph.create_formulary_order(
            encounter=labour, medication=self.med, doctor=self.doctor, route="ORAL",
            frequency="TDS", dose_text="1 capsule", days=3, doses_total=2, override="",
            staff=self.doctor)
        order = labour.medication_orders.get()
        ph.check_prepaid(order)  # must not raise: the bill is paid later

    def test_unpaid_ordinary_medicine_is_still_blocked(self):
        self.stock(serials=("SN-1",), per_unit=10)
        self.prescribe(doses=2)
        order = self.encounter.medication_orders.get()
        with self.assertRaises(ph.PrepayError):
            ph.check_prepaid(order)


class ReceiveValidationTests(PharmacyBase):
    """Server-side refusals for stock receiving (the service is the authority, not the form)."""

    def test_expiry_more_than_ten_years_away_is_refused(self):
        with self.assertRaises(ph.PharmacyError):
            ph.receive_batch(medication=self.med, batch_number="FAR", expiry_date=date(9999, 1, 1),
                             serials=["SN-FAR"], doses_per_unit=10, staff=self.pharmacist)

    def test_absurd_doses_per_unit_is_refused(self):
        with self.assertRaises(ph.PharmacyError):
            ph.receive_batch(medication=self.med, batch_number="DPU", expiry_date=_future(),
                             serials=["SN-DPU"], doses_per_unit=-3, staff=self.pharmacist)
        with self.assertRaises(ph.PharmacyError):
            ph.receive_batch(medication=self.med, batch_number="DPU2", expiry_date=_future(),
                             serials=["SN-DPU2"], doses_per_unit=50000, staff=self.pharmacist)

    def test_overlong_batch_number_is_refused(self):
        with self.assertRaises(ph.PharmacyError):
            ph.receive_batch(medication=self.med, batch_number="X" * 41, expiry_date=_future(),
                             serials=["SN-LONG"], doses_per_unit=10, staff=self.pharmacist)
