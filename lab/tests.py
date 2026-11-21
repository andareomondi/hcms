"""
Laboratory rules: tests are processed only once paid, a result is verified by a
different person from the one who entered it, reports have a minimum length,
and amendments need a reason and a different person from the original entrant.
"""
from decimal import Decimal

from django.test import TestCase

from billing import services as bill
from billing.payments import take_payment
from hospital_system.testing import make_encounter, make_patient, make_staff
from lab import services as lab
from lab.models import LabTest


class LabBase(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.doctor = make_staff("DOCTOR")
        self.billing = make_staff("BILLING")
        self.tech = make_staff("LAB_TECHNICIAN")
        self.tech2 = make_staff("LAB_TECHNICIAN")
        self.supervisor = make_staff("LAB_SUPERVISOR")
        self.supervisor2 = make_staff("LAB_SUPERVISOR")
        self.patient = make_patient(self.reception)
        self.encounter = make_encounter(self.patient, self.reception)
        self.test = LabTest.objects.create(name="Full Blood Count", code="FBC", price=Decimal("800.00"))

    def order(self, tests=None):
        order, _ = lab.order_tests(encounter=self.encounter, doctor=self.doctor,
                                   test_ids=[t.id for t in (tests or [self.test])],
                                   indication="Fever for three days", staff=self.doctor)
        return order

    def pay_all(self):
        for c in bill.open_lines(self.encounter):
            if c.status == "PROVISIONAL":
                bill.post_charge_for_release(c, self.billing)
        take_payment(encounter=self.encounter, method="MANUAL",
                     amount_raw=str(bill.encounter_tally(self.encounter)["outstanding"]),
                     reference="", payer_phone="", staff=self.billing)


class OrderingTests(LabBase):
    def test_order_needs_a_clinical_indication(self):
        with self.assertRaises(lab.LabError):
            lab.order_tests(encounter=self.encounter, doctor=self.doctor,
                            test_ids=[self.test.id], indication="  ", staff=self.doctor)

    def test_order_needs_at_least_one_test(self):
        with self.assertRaises(lab.LabError):
            lab.order_tests(encounter=self.encounter, doctor=self.doctor,
                            test_ids=[], indication="Fever", staff=self.doctor)

    def test_each_test_is_its_own_charge(self):
        second = LabTest.objects.create(name="Malaria Smear", code="MAL", price=Decimal("300.00"))
        self.order(tests=[self.test, second])
        lab_charges = bill.Charge.objects.filter(source_type="LAB_TEST")
        self.assertEqual(lab_charges.count(), 2)
        self.assertEqual(sum(c.amount for c in lab_charges), Decimal("1100.00"))


class ReceptionOfSampleTests(LabBase):
    def test_sample_is_not_received_before_payment(self):
        order = self.order()
        with self.assertRaises(lab.LabError):
            lab.receive_order(order=order, staff=self.tech)

    def test_sample_is_received_once_the_test_is_paid(self):
        order = self.order()
        self.pay_all()
        lab.receive_order(order=order, staff=self.tech)
        order.refresh_from_db()
        self.assertEqual(order.status, "RECEIVED")

    def test_cancelled_order_cannot_be_received(self):
        order = self.order()
        order.status = "CANCELLED"
        order.save()
        with self.assertRaises(lab.LabError):
            lab.receive_order(order=order, staff=self.tech)


class ReportAndReleaseTests(LabBase):
    def setUp(self):
        super().setUp()
        self.order_obj = self.order()
        self.pay_all()
        lab.receive_order(order=self.order_obj, staff=self.tech)
        line = self.order_obj.lines.get()
        lab.enter_result(line=line, staff=self.tech, value_raw="12.5")

    def test_report_shorter_than_minimum_is_refused(self):
        with self.assertRaises(lab.LabError):
            lab.submit_report(order=self.order_obj, staff=self.tech, text="Too short")

    def test_report_is_accepted_at_minimum_length(self):
        text = "x" * lab.REPORT_MIN_CHARS
        lab.submit_report(order=self.order_obj, staff=self.tech, text=text)
        self.order_obj.refresh_from_db()
        self.assertEqual(self.order_obj.report, text)

    def test_release_needs_a_report_first(self):
        with self.assertRaises(lab.LabError):
            lab.verify_and_release(order=self.order_obj, supervisor=self.supervisor)

    def test_person_who_entered_results_cannot_verify_them(self):
        lab.submit_report(order=self.order_obj, staff=self.tech, text="x" * lab.REPORT_MIN_CHARS)
        self.order_obj.refresh_from_db()
        supervisor_who_entered = make_staff("LAB_SUPERVISOR")
        line = self.order_obj.lines.get()
        line.entered_by = supervisor_who_entered
        line.save()
        with self.assertRaises(lab.LabError):
            lab.verify_and_release(order=self.order_obj, supervisor=supervisor_who_entered)

    def test_a_different_supervisor_can_release(self):
        lab.submit_report(order=self.order_obj, staff=self.tech, text="x" * lab.REPORT_MIN_CHARS)
        self.order_obj.refresh_from_db()
        lab.verify_and_release(order=self.order_obj, supervisor=self.supervisor)
        self.order_obj.refresh_from_db()
        self.assertEqual(self.order_obj.status, "RELEASED")
        self.assertEqual(self.order_obj.verified_by, self.supervisor)


class AmendmentTests(LabBase):
    def setUp(self):
        super().setUp()
        self.order_obj = self.order()
        self.pay_all()
        lab.receive_order(order=self.order_obj, staff=self.tech)
        self.line = self.order_obj.lines.get()
        lab.enter_result(line=self.line, staff=self.tech, value_raw="12.5")
        lab.submit_report(order=self.order_obj, staff=self.tech, text="x" * lab.REPORT_MIN_CHARS)
        self.order_obj.refresh_from_db()
        lab.verify_and_release(order=self.order_obj, supervisor=self.supervisor)
        self.line.refresh_from_db()

    def test_amendment_needs_a_reason(self):
        with self.assertRaises(lab.LabError):
            lab.amend_result(line=self.line, supervisor=self.supervisor2, value_raw="13", reason="")

    def test_original_entrant_cannot_amend_their_own_result(self):
        with self.assertRaises(lab.LabError):
            lab.amend_result(line=self.line, supervisor=self.tech, value_raw="13", reason="Typo")

    def test_amendment_keeps_the_original_value(self):
        from lab.models import LabResultAmendment
        lab.amend_result(line=self.line, supervisor=self.supervisor2, value_raw="13", reason="Typo")
        self.assertEqual(LabResultAmendment.objects.count(), 1)
        amendment = LabResultAmendment.objects.get()
        self.assertIn("12.5", str(amendment.__dict__.values()))
