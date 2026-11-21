"""
Billing rules: provisional holds, posting by a second person, allocation order,
per-line release, payments, refunds, and guardian/payer rules.
"""
from decimal import Decimal

from django.test import TestCase

from billing import services as bill
from billing.models import Charge, Payment, PaymentAllocation, Refund
from billing.payments import PaymentError, take_payment
from billing.refunds import RefundError, pay_refund, close_order_charge
from core_patients.models import PatientRelationship
from hospital_system.testing import make_encounter, make_patient, make_staff, years_ago


class ChargeCreationTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.patient = make_patient(self.reception)
        self.encounter = make_encounter(self.patient, self.reception)

    def test_new_charge_is_provisional_and_priced_by_quantity(self):
        charge = bill.add_charge(encounter=self.encounter, source_type="MEDICATION_ORDER",
                                 description="Amoxicillin", unit_price=Decimal("50.00"),
                                 quantity=Decimal("3"), staff=self.reception)
        self.assertEqual(charge.status, "PROVISIONAL")
        self.assertEqual(charge.amount, Decimal("150.00"))

    def test_same_idempotency_key_returns_existing_charge(self):
        first = bill.add_charge(encounter=self.encounter, source_type="SCAN_ORDER",
                                description="X-Ray", unit_price=Decimal("1500"),
                                staff=self.reception, key="scan:abc")
        second = bill.add_charge(encounter=self.encounter, source_type="SCAN_ORDER",
                                 description="X-Ray", unit_price=Decimal("1500"),
                                 staff=self.reception, key="scan:abc")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Charge.objects.filter(idempotency_key="scan:abc").count(), 1)

    def test_billing_account_is_created_for_the_patient(self):
        charge = bill.add_charge(encounter=self.encounter, source_type="CONSULTATION",
                                 description="Consultation", unit_price=Decimal("1000"),
                                 staff=self.reception)
        self.assertEqual(charge.billing_account.owner, self.patient)


class PostingTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.doctor = make_staff("DOCTOR")
        self.billing = make_staff("BILLING")
        self.patient = make_patient(self.reception)
        self.encounter = make_encounter(self.patient, self.reception)

    def _charge(self, staff, source="SCAN_ORDER", price="1500"):
        return bill.add_charge(encounter=self.encounter, source_type=source,
                               description="Scan", unit_price=Decimal(price), staff=staff)

    def test_billing_officer_can_post_a_provisional_charge(self):
        charge = self._charge(self.doctor)
        posted = bill.post_charge_for_release(charge, self.billing)
        self.assertEqual(posted.status, "POSTED")
        self.assertIsNotNone(posted.posted_at)

    def test_person_who_created_the_charge_cannot_post_it(self):
        charge = self._charge(self.doctor)
        with self.assertRaises(ValueError):
            bill.post_charge_for_release(charge, self.doctor)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "PROVISIONAL")

    def test_posting_an_already_posted_charge_is_a_no_op(self):
        charge = self._charge(self.doctor)
        bill.post_charge_for_release(charge, self.billing)
        again = bill.post_charge_for_release(charge, self.billing)
        self.assertEqual(again.status, "POSTED")


class AllocationOrderTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.billing = make_staff("BILLING")
        self.patient = make_patient(self.reception)
        self.encounter = make_encounter(self.patient, self.reception)

    def _add(self, source, price):
        return bill.add_charge(encounter=self.encounter, source_type=source,
                               description=source, unit_price=Decimal(price), staff=self.reception)

    def test_fees_are_settled_before_medicines_and_scans(self):
        scan = self._add("SCAN_ORDER", "1500")
        fee = self._add("CONSULTATION", "1000")
        order = [c.source_type for c in bill.open_lines(self.encounter)]
        self.assertEqual(order[0], "CONSULTATION")
        self.assertEqual(order[1], "SCAN_ORDER")

    def test_partial_payment_settles_fee_first_and_leaves_scan_unpaid(self):
        scan = self._add("SCAN_ORDER", "1500")
        fee = self._add("CONSULTATION", "1000")
        take_payment(encounter=self.encounter, method="MANUAL", amount_raw="1000",
                     reference="", payer_phone="", staff=self.billing)
        fee.refresh_from_db()
        scan.refresh_from_db()
        self.assertEqual(fee.status, "PAID")
        self.assertEqual(scan.status, "PROVISIONAL")
        self.assertFalse(bill.charge_fully_paid(scan))

    def test_partial_payment_on_a_line_does_not_release_it(self):
        scan = self._add("SCAN_ORDER", "1500")
        take_payment(encounter=self.encounter, method="MANUAL", amount_raw="500",
                     reference="", payer_phone="", staff=self.billing)
        self.assertFalse(bill.charge_fully_paid(scan))
        self.assertEqual(bill.outstanding_amount(scan), Decimal("1000.00"))

    def test_overpayment_is_refused(self):
        self._add("CONSULTATION", "1000")
        with self.assertRaises(PaymentError):
            take_payment(encounter=self.encounter, method="MANUAL", amount_raw="1000.01",
                         reference="", payer_phone="", staff=self.billing)
        self.assertEqual(Payment.objects.count(), 0)

    def test_payment_with_nothing_outstanding_is_refused(self):
        with self.assertRaises(PaymentError):
            take_payment(encounter=self.encounter, method="MANUAL", amount_raw="100",
                         reference="", payer_phone="", staff=self.billing)

    def test_emergency_bypassed_visit_settles_oldest_first(self):
        self.encounter.prepay_bypassed = True
        self.encounter.save()
        old = self._add("SCAN_ORDER", "1500")
        self._add("CONSULTATION", "1000")
        first = bill.open_lines(self.encounter)[0]
        self.assertEqual(first.pk, old.pk)


class RecordedPaymentTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.billing = make_staff("BILLING")
        self.patient = make_patient(self.reception)
        self.encounter = make_encounter(self.patient, self.reception)
        self.fee = bill.add_charge(encounter=self.encounter, source_type="CONSULTATION",
                                   description="Consultation", unit_price=Decimal("1000"),
                                   staff=self.reception)

    def test_self_payment_is_recorded_with_payer_details(self):
        payment, _ = take_payment(encounter=self.encounter, method="MANUAL", amount_raw="1000",
                                  reference="", payer_phone="", staff=self.billing)
        self.assertEqual(payment.payer_type, "SELF")
        self.assertEqual(payment.payer_patient, self.patient)
        self.assertEqual(payment.recorded_by, self.billing)
        self.assertTrue(payment.receipt_number.startswith("RCP-"))

    def test_zero_and_negative_amounts_are_refused(self):
        for amount in ("0", "-5"):
            with self.assertRaises(PaymentError):
                take_payment(encounter=self.encounter, method="MANUAL", amount_raw=amount,
                             reference="", payer_phone="", staff=self.billing)

    def test_invalid_amount_text_is_refused(self):
        with self.assertRaises(PaymentError):
            take_payment(encounter=self.encounter, method="MANUAL", amount_raw="abc",
                         reference="", payer_phone="", staff=self.billing)

    def test_mpesa_code_must_be_8_to_12_alphanumeric(self):
        for bad in ("ABC", "ABCDEFGHIJKLM", "abc-1234"):
            with self.assertRaises(PaymentError):
                take_payment(encounter=self.encounter, method="MPESA", amount_raw="100",
                             reference=bad, payer_phone="", staff=self.billing)

    def test_duplicate_mpesa_code_is_refused(self):
        take_payment(encounter=self.encounter, method="MPESA", amount_raw="400",
                     reference="QWE12RTY9", payer_phone="", staff=self.billing)
        with self.assertRaises(PaymentError):
            take_payment(encounter=self.encounter, method="MPESA", amount_raw="600",
                         reference="qwe12rty9", payer_phone="", staff=self.billing)

    def test_other_adult_payer_needs_name_relationship_and_id(self):
        base = dict(encounter=self.encounter, method="MANUAL", amount_raw="100",
                    reference="", payer_phone="", staff=self.billing, payer_type="OTHER")
        with self.assertRaises(PaymentError):
            take_payment(**base, payer_name="Jane", payer_relationship="", payer_id_number="1")
        with self.assertRaises(PaymentError):
            take_payment(**base, payer_name="Jane", payer_relationship="Sister", payer_id_number="")

    def test_guardian_payer_must_be_actively_linked(self):
        stranger = make_patient(self.reception, last_name="Stranger")
        with self.assertRaises(PaymentError):
            take_payment(encounter=self.encounter, method="MANUAL", amount_raw="100",
                         reference="", payer_phone="", staff=self.billing,
                         payer_type="GUARDIAN", payer_patient_id=str(stranger.pk))

    def test_guardian_payer_with_active_link_is_accepted(self):
        parent = make_patient(self.reception, last_name="Parent")
        PatientRelationship.objects.create(patient=self.patient, related_patient=parent,
                                           relationship="PARENT", is_active=True)
        payment, _ = take_payment(encounter=self.encounter, method="MANUAL", amount_raw="100",
                                  reference="", payer_phone="", staff=self.billing,
                                  payer_type="GUARDIAN", payer_patient_id=str(parent.pk))
        self.assertEqual(payment.payer_type, "GUARDIAN")
        self.assertEqual(payment.payer_patient, parent)

    def test_payment_by_a_creator_of_a_provisional_line_is_refused(self):
        with self.assertRaises(PaymentError):
            take_payment(encounter=self.encounter, method="MANUAL", amount_raw="1000",
                         reference="", payer_phone="", staff=self.reception)
        self.assertEqual(Payment.objects.count(), 0)


class VoidAndReversalTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.doctor = make_staff("DOCTOR")
        self.billing = make_staff("BILLING")
        self.patient = make_patient(self.reception)
        self.encounter = make_encounter(self.patient, self.reception)

    def test_provisional_charge_is_voided_and_leaves_the_total(self):
        charge = bill.add_charge(encounter=self.encounter, source_type="SCAN_ORDER",
                                 description="Scan", unit_price=Decimal("1500"), staff=self.doctor)
        bill.void_or_reverse(charge, self.doctor)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "VOIDED")
        self.assertEqual(bill.encounter_tally(self.encounter)["total"], Decimal("0.00"))

    def test_posted_charge_is_reversed_not_edited(self):
        charge = bill.add_charge(encounter=self.encounter, source_type="SCAN_ORDER",
                                 description="Scan", unit_price=Decimal("1500"), staff=self.doctor)
        posted = bill.post_charge_for_release(charge, self.billing)
        reversal = bill.void_or_reverse(posted, self.billing)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "POSTED")
        self.assertEqual(charge.amount, Decimal("1500.00"))
        self.assertEqual(reversal.amount, Decimal("-1500.00"))
        self.assertEqual(reversal.reversal_of, charge)
        self.assertEqual(bill.encounter_tally(self.encounter)["total"], Decimal("0.00"))


class RefundTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.doctor = make_staff("DOCTOR")
        self.billing = make_staff("BILLING")
        self.billing2 = make_staff("BILLING")
        self.patient = make_patient(self.reception)
        self.encounter = make_encounter(self.patient, self.reception)
        self.med = bill.add_charge(encounter=self.encounter, source_type="MEDICATION_ORDER",
                                   description="Drug", unit_price=Decimal("300"), staff=self.doctor)
        bill.post_charge_for_release(self.med, self.billing)
        take_payment(encounter=self.encounter, method="MANUAL", amount_raw="300",
                     reference="", payer_phone="", staff=self.billing)

    def test_cancelling_a_paid_medicine_requests_a_refund_and_keeps_the_charge(self):
        close_order_charge(charge=self.med, staff=self.doctor, reason="Out of stock")
        self.med.refresh_from_db()
        self.assertEqual(self.med.status, "PAID")
        refund = Refund.objects.get()
        self.assertEqual(refund.amount, Decimal("300.00"))
        self.assertEqual(refund.status, "PENDING")

    def test_pending_refund_does_not_change_the_balance(self):
        close_order_charge(charge=self.med, staff=self.doctor, reason="Out of stock")
        self.assertEqual(bill.encounter_tally(self.encounter)["outstanding"], Decimal("0.00"))

    def test_requester_cannot_pay_out_their_own_refund(self):
        close_order_charge(charge=self.med, staff=self.doctor, reason="Out of stock")
        refund = Refund.objects.get()
        with self.assertRaises(RefundError):
            pay_refund(refund=refund, staff=self.doctor, method="MANUAL")

    def test_paying_out_a_refund_reduces_the_visit_total_and_paid_amount(self):
        close_order_charge(charge=self.med, staff=self.doctor, reason="Out of stock")
        refund = Refund.objects.get()
        pay_refund(refund=refund, staff=self.billing2, method="MANUAL")
        tally = bill.encounter_tally(self.encounter)
        self.assertEqual(tally["refunded"], Decimal("300.00"))
        self.assertEqual(tally["total"], Decimal("0.00"))
        refund.refresh_from_db()
        self.assertEqual(refund.status, "PAID")

    def test_refund_cannot_be_paid_out_twice(self):
        close_order_charge(charge=self.med, staff=self.doctor, reason="Out of stock")
        refund = Refund.objects.get()
        pay_refund(refund=refund, staff=self.billing2, method="MANUAL")
        with self.assertRaises(RefundError):
            pay_refund(refund=refund, staff=self.billing2, method="MANUAL")

    def test_unpaid_medicine_is_voided_not_refunded(self):
        other = bill.add_charge(encounter=self.encounter, source_type="MEDICATION_ORDER",
                                description="Drug 2", unit_price=Decimal("200"), staff=self.doctor)
        close_order_charge(charge=other, staff=self.doctor, reason="Not supplied")
        other.refresh_from_db()
        self.assertEqual(other.status, "VOIDED")
        self.assertEqual(Refund.objects.count(), 0)


class PayerRulesTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")

    def test_adult_pays_for_themselves(self):
        adult = make_patient(self.reception, date_of_birth=years_ago(35))
        self.assertEqual(bill.billing_owner_for(adult), adult)

    def test_child_without_guarantor_is_held(self):
        child = make_patient(self.reception, date_of_birth=years_ago(6))
        self.assertIsNotNone(bill.billing_hold_reason(child))

    def test_child_pays_through_active_guarantor(self):
        child = make_patient(self.reception, date_of_birth=years_ago(6))
        parent = make_patient(self.reception, date_of_birth=years_ago(35))
        PatientRelationship.objects.create(patient=child, related_patient=parent, relationship="PARENT",
                                           is_billing_guarantor=True, is_active=True)
        self.assertEqual(bill.billing_owner_for(child), parent)
        self.assertIsNone(bill.billing_hold_reason(child))
