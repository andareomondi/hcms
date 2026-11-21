"""
Visit rules: one open visit per patient, triage before consultation, emergencies
treated before payment only with a reason, the visit waits for fees, and a
consultation cannot close while an investigation is outstanding.
"""
from decimal import Decimal

from django.test import TestCase

from billing import services as bill
from billing.payments import take_payment
from hospital_system.testing import make_encounter, make_patient, make_staff
from triage_queue import intake
from triage_queue.models import Encounter, QueueEntry
from triage_queue.services import FlowError

VITALS = dict(systolic_mmhg=120, diastolic_mmhg=80, pulse_bpm=80, temperature_c=36.8,
              respiratory_rate=16, spo2_percent=98, weight_kg=60)


def clock_in_doctor(doctor):
    from core_patients.models import Department
    from duty.models import Room
    from duty import services as duty
    dept, _ = Department.objects.get_or_create(code="CLN", defaults={"name": "Clinic", "kind": "CLINICAL"})
    import uuid
    # Room.code is max 20 chars; a UUID pk would overflow it on PostgreSQL.
    room = Room.objects.create(department=dept, code=f"R{uuid.uuid4().hex[:8]}", name="Room")
    duty.clock_in(doctor, room)


class VisitOpeningTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.patient = make_patient(self.reception)

    def test_unknown_visit_type_is_refused(self):
        with self.assertRaises(FlowError):
            intake.open_visit(patient=self.patient, encounter_type="BOGUS",
                              payer_type="SELF_PAY", staff=self.reception)

    def test_ward_stay_cannot_be_opened_from_reception(self):
        with self.assertRaises(FlowError):
            intake.open_visit(patient=self.patient, encounter_type="WARD_STAY",
                              payer_type="SELF_PAY", staff=self.reception)

    def test_second_open_visit_for_the_same_patient_is_refused(self):
        intake.open_visit(patient=self.patient, encounter_type="OUTPATIENT",
                          payer_type="SELF_PAY", staff=self.reception)
        with self.assertRaises(FlowError):
            intake.open_visit(patient=self.patient, encounter_type="OUTPATIENT",
                              payer_type="SELF_PAY", staff=self.reception)

    def test_opening_a_visit_charges_the_registration_and_consultation_fees(self):
        intake.open_visit(patient=self.patient, encounter_type="OUTPATIENT",
                          payer_type="SELF_PAY", staff=self.reception)
        sources = sorted(c.source_type for c in bill.Charge.objects.all())
        self.assertEqual(sources, ["CONSULTATION", "REGISTRATION"])

    def test_inpatient_review_refused_for_a_patient_not_admitted(self):
        with self.assertRaises(FlowError):
            intake.open_visit(patient=self.patient, encounter_type="INPATIENT",
                              payer_type="SELF_PAY", staff=self.reception)


class TriageAndFeeGateTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.triage_nurse = make_staff("TRIAGE_NURSE")
        self.doctor = make_staff("DOCTOR")
        clock_in_doctor(self.doctor)
        self.billing = make_staff("BILLING")
        self.patient = make_patient(self.reception)
        self.encounter = intake.open_visit(patient=self.patient, encounter_type="OUTPATIENT",
                                           payer_type="SELF_PAY", staff=self.reception)

    def triage(self, priority=1, bypass_reason=""):
        from triage_queue.intake import triage_encounter
        return triage_encounter(encounter=self.encounter, staff=self.triage_nurse, doctor=self.doctor,
                                priority=priority, vitals=VITALS, chief_complaint="Cough",
                                bypass_reason=bypass_reason)

    def test_patient_waits_for_payment_before_joining_the_queue(self):
        self.triage()
        entry = QueueEntry.objects.get(encounter=self.encounter)
        self.assertEqual(entry.status, "AWAITING_PAYMENT")

    def test_patient_joins_the_queue_once_fees_are_paid(self):
        self.triage()
        for c in bill.open_lines(self.encounter):
            bill.post_charge_for_release(c, self.billing)
        take_payment(encounter=self.encounter, method="MANUAL",
                     amount_raw=str(bill.encounter_tally(self.encounter)["outstanding"]),
                     reference="", payer_phone="", staff=self.billing)
        entry = QueueEntry.objects.get(encounter=self.encounter)
        self.assertEqual(entry.status, "WAITING")

    def test_emergency_without_a_reason_is_refused(self):
        with self.assertRaises(FlowError):
            self.triage(priority=3, bypass_reason="")

    def test_emergency_with_reason_bypasses_payment_and_is_queued_for_billing(self):
        self.encounter.encounter_type = "EMERGENCY"
        self.encounter.save()
        self.triage(priority=3, bypass_reason="Road traffic accident, unconscious")
        self.encounter.refresh_from_db()
        self.assertTrue(self.encounter.prepay_bypassed)
        entry = QueueEntry.objects.get(encounter=self.encounter)
        self.assertEqual(entry.status, "WAITING")

    def test_patient_cannot_be_triaged_twice(self):
        self.triage()
        with self.assertRaises(FlowError):
            self.triage()

    def test_a_non_doctor_cannot_be_assigned_as_the_consulting_doctor(self):
        receptionist = make_staff("RECEPTION")
        from triage_queue.intake import triage_encounter
        with self.assertRaises(FlowError):
            triage_encounter(encounter=self.encounter, staff=self.triage_nurse, doctor=receptionist,
                             priority=1, vitals=VITALS, chief_complaint="Cough")

    def test_a_doctor_who_is_not_clocked_in_cannot_be_assigned(self):
        off_duty = make_staff("DOCTOR")
        from triage_queue.intake import triage_encounter
        with self.assertRaises(FlowError):
            triage_encounter(encounter=self.encounter, staff=self.triage_nurse, doctor=off_duty,
                             priority=1, vitals=VITALS, chief_complaint="Cough")

    def test_an_inactive_doctor_cannot_be_assigned(self):
        inactive = make_staff("DOCTOR")
        clock_in_doctor(inactive)
        inactive.is_active = False
        inactive.save()
        from triage_queue.intake import triage_encounter
        with self.assertRaises(FlowError):
            triage_encounter(encounter=self.encounter, staff=self.triage_nurse, doctor=inactive,
                             priority=1, vitals=VITALS, chief_complaint="Cough")
