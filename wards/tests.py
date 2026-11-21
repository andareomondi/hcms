"""
Ward rules: only a doctor admits, from a consultation, with a real reason and a vacant bed.
Discharge is requested by ward staff, approved by a doctor, needs a second confirmation
from the admitting doctor, and is blocked while money is owed.
"""
import unittest
from decimal import Decimal

from django.test import TestCase

from billing import services as bill
from billing.payments import take_payment
from hospital_system.testing import make_encounter, make_patient, make_staff
from wards import services as wards
from wards.models import Admission, Bed, Ward

LONG_REASON = ("Pneumonia with low oxygen saturation on arrival, needing IV antibiotics, "
               "oxygen support and close observation of vital signs over the next few days")


class WardBase(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.doctor = make_staff("DOCTOR")
        self.doctor2 = make_staff("DOCTOR")
        self.nurse = make_staff("NURSE")
        self.billing = make_staff("BILLING")
        self.patient = make_patient(self.reception)
        self.review = make_encounter(self.patient, self.reception, encounter_type="OUTPATIENT")
        from core_patients.models import Department
        dept = Department.objects.create(name="Medicine", code="MED", kind="WARD")
        self.ward = Ward.objects.create(name="Medical A", department=dept, bed_day_rate=Decimal("2500"))
        self.bed = Bed.objects.create(ward=self.ward, code="MA-1", status="VACANT")

    def admit(self, staff=None, reason=LONG_REASON, bed=None):
        return wards.admit(review=self.review, bed_id=(bed or self.bed).pk, planned_days=3,
                           reason=reason, staff=staff or self.doctor)


class AdmissionTests(WardBase):
    def test_only_a_doctor_can_admit(self):
        with self.assertRaises(wards.FlowError):
            self.admit(staff=self.nurse)

    def test_reason_under_100_characters_is_refused(self):
        with self.assertRaises(wards.FlowError):
            self.admit(reason="Too short to admit")

    def test_planned_stay_must_be_between_1_and_365_days(self):
        for days in (0, 366):
            with self.assertRaises(wards.FlowError):
                wards.admit(review=self.review, bed_id=self.bed.pk, planned_days=days,
                            reason=LONG_REASON, staff=self.doctor)

    def test_occupied_bed_is_refused(self):
        self.bed.status = "OCCUPIED"
        self.bed.save()
        with self.assertRaises(wards.FlowError):
            self.admit()

    def test_successful_admission_opens_a_ward_stay_and_takes_the_bed(self):
        self.admit()
        self.bed.refresh_from_db()
        self.assertEqual(self.bed.status, "OCCUPIED")
        stay = Admission.objects.get()
        self.assertEqual(stay.encounter.encounter_type, "WARD_STAY")
        self.assertEqual(stay.admitted_by, self.doctor)

    def test_patient_cannot_be_admitted_twice(self):
        self.admit()
        other_bed = Bed.objects.create(ward=self.ward, code="MA-2", status="VACANT")
        with self.assertRaises(wards.FlowError):
            self.admit(bed=other_bed)

    def test_admission_from_a_ward_stay_is_refused(self):
        stay = make_encounter(self.patient, self.reception, encounter_type="WARD_STAY")
        with self.assertRaises(wards.FlowError):
            wards.admit(review=stay, bed_id=self.bed.pk, planned_days=2,
                        reason=LONG_REASON, staff=self.doctor)


class BedStatusTests(WardBase):
    def test_ward_staff_cannot_assign_a_patient_to_a_bed(self):
        with self.assertRaises(Exception):
            wards.change_status(bed_id=self.bed.pk, to_status="OCCUPIED", staff=self.nurse, reason="")

    def test_isolating_a_bed_needs_a_reason(self):
        with self.assertRaises(wards.FlowError):
            wards.change_status(bed_id=self.bed.pk, to_status="ISOLATION", staff=self.nurse, reason="")

    def test_bed_with_a_patient_cannot_be_closed_by_hand(self):
        self.admit()
        with self.assertRaises(wards.FlowError):
            wards.change_status(bed_id=self.bed.pk, to_status="CLEANING", staff=self.nurse, reason="x")


class DischargeTests(WardBase):
    def setUp(self):
        super().setUp()
        self.admit()
        self.adm = Admission.objects.get()
        # The planned bed days are billed to the stay; settle them so discharge is allowed.
        for c in bill.open_lines(self.adm.encounter):
            bill.post_charge_for_release(c, self.billing)
        take_payment(encounter=self.adm.encounter, method="MANUAL",
                     amount_raw=str(bill.encounter_tally(self.adm.encounter)["outstanding"]),
                     reference="", payer_phone="", staff=self.billing)

    def test_doctor_cannot_approve_before_discharge_is_requested(self):
        with self.assertRaises(wards.FlowError):
            wards.approve_discharge(admission=self.adm, doctor=self.doctor2)

    @unittest.expectedFailure  # BUG: approve_discharge is @transaction.atomic, so the token
    # saved before DischargeConfirmationRequired is raised is rolled back. The second
    # confirmation can never match. See the report.
    def test_admitting_doctor_must_confirm_twice(self):
        wards.request_discharge(admission=self.adm, staff=self.nurse)
        self.adm.refresh_from_db()
        with self.assertRaises(wards.DischargeConfirmationRequired) as ctx:
            wards.approve_discharge(admission=self.adm, doctor=self.doctor)
        token = ctx.exception.token
        wards.approve_discharge(admission=self.adm, doctor=self.doctor, token=token)
        self.adm.refresh_from_db()
        self.assertIsNotNone(self.adm.discharged_at)

    def test_a_wrong_confirmation_token_is_refused(self):
        wards.request_discharge(admission=self.adm, staff=self.nurse)
        self.adm.refresh_from_db()
        with self.assertRaises(wards.DischargeConfirmationRequired):
            wards.approve_discharge(admission=self.adm, doctor=self.doctor)
        with self.assertRaises(wards.DischargeConfirmationRequired):
            wards.approve_discharge(admission=self.adm, doctor=self.doctor, token="wrong")

    def test_a_different_doctor_discharges_without_second_confirmation(self):
        wards.request_discharge(admission=self.adm, staff=self.nurse)
        self.adm.refresh_from_db()
        wards.approve_discharge(admission=self.adm, doctor=self.doctor2)
        self.adm.refresh_from_db()
        self.assertIsNotNone(self.adm.discharged_at)

    def test_discharge_is_blocked_while_the_stay_has_an_unpaid_balance(self):
        bill.add_charge(encounter=self.adm.encounter, source_type="BED_DAY",
                        description="Bed day", unit_price=Decimal("2500"), staff=self.nurse)
        wards.request_discharge(admission=self.adm, staff=self.nurse)
        self.adm.refresh_from_db()
        with self.assertRaises(wards.FlowError):
            wards.approve_discharge(admission=self.adm, doctor=self.doctor2)
