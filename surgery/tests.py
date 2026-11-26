from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from core_patients.models import Allergen, AllergenClass, PatientAllergy
from hospital_system.testing import make_patient, make_staff
from triage_queue import intake
from triage_queue.intake import FlowError
from triage_queue.models import Encounter
from billing.models import Charge

from . import services
from .models import SurgicalCase, SurgicalProcedure
from .services import SurgeryError


class SurgeryTestBase(TestCase):
    def setUp(self):
        self.doctor = make_staff("DOCTOR")
        self.patient = make_patient(self.doctor, sex="F")
        self.procedure = SurgicalProcedure.objects.create(
            name="Appendicectomy", category="INTERMEDIATE", price=Decimal("45000.00"))
        self.indication = Encounter.objects.create(
            patient=self.patient, encounter_type="OUTPATIENT", payer_type="SELF_PAY", opened_by=self.doctor)

    def _allergy(self, patient=None):
        cls = AllergenClass.objects.create(code="DRUG", name="Drug")
        allergen = Allergen.objects.create(code="PENICILLIN", display_name="Penicillin", allergen_class=cls)
        return PatientAllergy.objects.create(patient=patient or self.patient, allergen=allergen,
                                             severity="SEVERE", status="ACTIVE", recorded_by=self.doctor)


class BookingTests(SurgeryTestBase):
    def test_elective_booking_creates_closed_billing_container_and_fee(self):
        case = services.book_case(patient=self.patient, procedure=self.procedure, category="ELECTIVE",
                                  booked_by=self.doctor, indication_encounter=self.indication)
        self.assertEqual(case.billing_encounter.encounter_type, "SURGERY")
        self.assertEqual(case.billing_encounter.status, "CLOSED")
        charge = Charge.objects.get(encounter=case.billing_encounter)
        self.assertEqual(charge.source_type, "SURGERY")
        self.assertEqual(charge.unit_price, Decimal("45000.00"))

    def test_elective_without_indication_is_refused(self):
        with self.assertRaises(SurgeryError):
            services.book_case(patient=self.patient, procedure=self.procedure, category="ELECTIVE",
                               booked_by=self.doctor)

    def test_indication_from_another_patient_is_refused(self):
        other = make_patient(self.doctor, sex="F")
        other_visit = Encounter.objects.create(patient=other, encounter_type="OUTPATIENT",
                                               payer_type="SELF_PAY", opened_by=self.doctor)
        with self.assertRaises(SurgeryError):
            services.book_case(patient=self.patient, procedure=self.procedure, category="ELECTIVE",
                               booked_by=self.doctor, indication_encounter=other_visit)

    def test_emergency_needs_no_indication(self):
        case = services.book_case(patient=self.patient, procedure=self.procedure, category="EMERGENCY",
                                  booked_by=self.doctor)
        self.assertEqual(case.category, "EMERGENCY")

    def test_active_allergy_needs_a_doctor_and_a_note(self):
        self._allergy()
        nurse = make_staff("NURSE")
        with self.assertRaises(SurgeryError):
            services.book_case(patient=self.patient, procedure=self.procedure, category="EMERGENCY",
                               booked_by=nurse, allergy_clearance_note="A reason that is long enough.")
        with self.assertRaises(SurgeryError):
            services.book_case(patient=self.patient, procedure=self.procedure, category="EMERGENCY",
                               booked_by=self.doctor, allergy_clearance_note="too short")
        case = services.book_case(patient=self.patient, procedure=self.procedure, category="EMERGENCY",
                                  booked_by=self.doctor,
                                  allergy_clearance_note="Allergy reviewed; alternative antibiotic chosen.")
        self.assertEqual(case.allergy_clearance_by, self.doctor)

    def test_inactive_procedure_is_refused(self):
        self.procedure.is_active = False
        self.procedure.save()
        with self.assertRaises(SurgeryError):
            services.book_case(patient=self.patient, procedure=self.procedure, category="EMERGENCY",
                               booked_by=self.doctor)


class CancellationTests(SurgeryTestBase):
    def test_cancel_needs_a_reason_and_keeps_the_record(self):
        case = services.book_case(patient=self.patient, procedure=self.procedure, category="EMERGENCY",
                                  booked_by=self.doctor)
        with self.assertRaises(SurgeryError):
            services.cancel_case(case=case, cancelled_by=self.doctor, reason="no")
        services.cancel_case(case=case, cancelled_by=self.doctor,
                             reason="Patient went home and declined the operation.")
        case.refresh_from_db()
        self.assertEqual(case.status, "CANCELLED")
        self.assertTrue(SurgicalCase.objects.filter(pk=case.pk).exists())

    def test_cancelled_case_cannot_be_cancelled_again(self):
        case = services.book_case(patient=self.patient, procedure=self.procedure, category="EMERGENCY",
                                  booked_by=self.doctor)
        services.cancel_case(case=case, cancelled_by=self.doctor, reason="Patient declined the operation.")
        with self.assertRaises(SurgeryError):
            services.cancel_case(case=case, cancelled_by=self.doctor, reason="Patient declined the operation.")


class TriageExclusionTests(SurgeryTestBase):
    def test_surgery_encounter_never_appears_on_the_triage_list(self):
        case = services.book_case(patient=self.patient, procedure=self.procedure, category="EMERGENCY",
                                  booked_by=self.doctor)
        self.assertNotIn(case.billing_encounter.pk, [e.pk for e in intake.awaiting_triage()])

    def test_a_visit_cannot_be_opened_as_surgery(self):
        with self.assertRaises(FlowError):
            intake.open_visit(patient=self.patient, encounter_type="SURGERY", payer_type="SELF_PAY",
                              staff=self.doctor)

    def test_surgery_container_does_not_block_a_normal_visit(self):
        services.book_case(patient=self.patient, procedure=self.procedure, category="EMERGENCY",
                           booked_by=self.doctor)
        other_patient = make_patient(self.doctor, sex="F")
        self.assertIsNotNone(intake.open_visit(patient=other_patient, encounter_type="OUTPATIENT",
                                               payer_type="SELF_PAY", staff=self.doctor))


from datetime import timedelta as _td

from django.utils import timezone as _tz

from hospital_system.permissions import Cap, caps_for
from surgery import clinical
from surgery.models import (
    PreOpCheck, SurgicalConsent, SurgicalSignOff, SurgicalTeamMember, TheatreRecord, TheatreStockBatch,
    TheatreStockItem,
)
from billing import services as billing_services
from core_patients.models import Patient
from datetime import date as _date


def _adult(self):
    """Patient is an adult by default in these tests."""
    self.patient.date_of_birth = _date(1980, 1, 1)
    self.patient.save()


class TheatreBase(SurgeryTestBase):
    def setUp(self):
        super().setUp()
        _adult(self)
        self.surgeon = make_staff("DOCTOR")
        self.anaesthetist = make_staff("DOCTOR")
        self.nurse = make_staff("NURSE")
        self.case = services.book_case(patient=self.patient, procedure=self.procedure, category="ELECTIVE",
                                       booked_by=self.doctor, indication_encounter=self.indication)
        self._pay_off()

    def _pay_off(self):
        # A zero-value procedure for the default flow; the payment gate has its own tests.
        self.procedure.price = Decimal("0.00")
        self.procedure.save()
        self.case.billing_encounter.charges.update(amount=Decimal("0.00"), unit_price=Decimal("0.00"))

    def _team(self):
        clinical.add_team_member(case=self.case, staff_profile=self.surgeon, role="SURGEON", added_by=self.doctor)
        clinical.add_team_member(case=self.case, staff_profile=self.anaesthetist, role="ANAESTHETIST",
                                 added_by=self.doctor)
        clinical.add_team_member(case=self.case, staff_profile=self.nurse, role="CIRCULATING_NURSE",
                                 added_by=self.doctor)

    def _ready(self):
        self._team()
        clinical.record_pre_op(case=self.case, staff=self.doctor, fasting_hours="8",
                               vitals={"pulse_bpm": "80", "systolic_mmhg": "120", "diastolic_mmhg": "80"})
        clinical.record_consent(case=self.case, staff=self.doctor, signer_name="Test Patient",
                                relationship="SELF", is_guardian=False, typed_signature="Test Patient")

    def _sign_in(self):
        self._ready()
        return clinical.sign_in(case=self.case, staff=self.surgeon)

    def _through_sign_out(self):
        self._sign_in()
        note = "Identity, site and allergies confirmed"
        clinical.time_out(case=self.case, staff=self.surgeon, note=note)
        clinical.incision(case=self.case, staff=self.surgeon)
        clinical.closure(case=self.case, staff=self.surgeon)
        clinical.sign_out(case=self.case, staff=self.surgeon, note="Counts correct at sign-out")


class PreOpTests(TheatreBase):
    def test_elective_needs_six_hours_fasting(self):
        with self.assertRaises(SurgeryError):
            clinical.record_pre_op(case=self.case, staff=self.doctor, fasting_hours="3",
                                   vitals={"pulse_bpm": "80", "systolic_mmhg": "120"})

    def test_pulse_and_systolic_required(self):
        with self.assertRaises(SurgeryError):
            clinical.record_pre_op(case=self.case, staff=self.doctor, fasting_hours="8",
                                   vitals={"pulse_bpm": "", "systolic_mmhg": "120"})

    def test_out_of_range_vitals_refused(self):
        with self.assertRaises(ValueError):
            clinical.record_pre_op(case=self.case, staff=self.doctor, fasting_hours="8",
                                   vitals={"pulse_bpm": "900", "systolic_mmhg": "120"})

    def test_recorded_once(self):
        vitals = {"pulse_bpm": "80", "systolic_mmhg": "120"}
        clinical.record_pre_op(case=self.case, staff=self.doctor, fasting_hours="8", vitals=vitals)
        with self.assertRaises(SurgeryError):
            clinical.record_pre_op(case=self.case, staff=self.doctor, fasting_hours="8", vitals=vitals)


class ConsentTests(TheatreBase):
    def test_adult_signs_own_consent_with_typed_name(self):
        c = clinical.record_consent(case=self.case, staff=self.doctor, signer_name="Test Patient",
                                    relationship="SELF", is_guardian=False, typed_signature="test  patient")
        self.assertEqual(c.relationship, "SELF")

    def test_typed_signature_must_match(self):
        with self.assertRaises(SurgeryError):
            clinical.record_consent(case=self.case, staff=self.doctor, signer_name="Test Patient",
                                    relationship="SELF", is_guardian=False, typed_signature="Someone Else")

    def test_consent_cannot_be_changed(self):
        clinical.record_consent(case=self.case, staff=self.doctor, signer_name="Test Patient",
                                relationship="SELF", is_guardian=False, typed_signature="Test Patient")
        with self.assertRaises(SurgeryError):
            clinical.record_consent(case=self.case, staff=self.doctor, signer_name="Test Patient",
                                    relationship="SELF", is_guardian=False, typed_signature="Test Patient")

    def test_adult_cannot_be_consented_by_another(self):
        with self.assertRaises(SurgeryError):
            clinical.record_consent(case=self.case, staff=self.doctor, signer_name="Jane Other",
                                    relationship="OTHER", is_guardian=False, typed_signature="Jane Other")

    def test_minor_needs_guardian_and_cannot_self_consent(self):
        child = make_patient(self.doctor, sex="M", date_of_birth=_date(2018, 1, 1))
        enc = Encounter.objects.create(patient=child, encounter_type="OUTPATIENT", payer_type="SELF_PAY",
                                       opened_by=self.doctor)
        case = services.book_case(patient=child, procedure=self.procedure, category="ELECTIVE",
                                  booked_by=self.doctor, indication_encounter=enc)
        with self.assertRaises(SurgeryError):
            clinical.record_consent(case=case, staff=self.doctor, signer_name="Child Patient",
                                    relationship="SELF", is_guardian=False, typed_signature="Child Patient")
        with self.assertRaises(SurgeryError):
            clinical.record_consent(case=case, staff=self.doctor, signer_name="Mum Patient",
                                    relationship="PARENT", is_guardian=False, typed_signature="Mum Patient")
        c = clinical.record_consent(case=case, staff=self.doctor, signer_name="Mum Patient",
                                    relationship="PARENT", is_guardian=True, typed_signature="Mum Patient")
        self.assertTrue(c.is_guardian)

    def test_unknown_birth_date_refused(self):
        self.patient.date_of_birth = None
        self.patient.save()
        with self.assertRaises(SurgeryError):
            clinical.record_consent(case=self.case, staff=self.doctor, signer_name="Test Patient",
                                    relationship="SELF", is_guardian=False, typed_signature="Test Patient")


class PaymentGateTests(TheatreBase):
    def _set_price(self, amount):
        self.case.billing_encounter.charges.update(amount=Decimal(amount), unit_price=Decimal(amount))

    def _ready_team_and_checks(self):
        self._team()
        clinical.record_pre_op(case=self.case, staff=self.doctor, fasting_hours="8",
                               vitals={"pulse_bpm": "80", "systolic_mmhg": "120"})
        clinical.record_consent(case=self.case, staff=self.doctor, signer_name="Test Patient",
                                relationship="SELF", is_guardian=False, typed_signature="Test Patient")

    def test_elective_refused_while_unpaid(self):
        self._set_price("5000.00")
        self._ready_team_and_checks()
        with self.assertRaises(SurgeryError):
            clinical.sign_in(case=self.case, staff=self.surgeon)

    def test_elective_starts_once_paid(self):
        self._set_price("5000.00")
        self._ready_team_and_checks()
        from billing.payments import take_payment
        take_payment(encounter=self.case.billing_encounter, method="MANUAL", amount_raw="5000.00",
                     reference="T-SURG-1", payer_phone="", staff=make_staff("BILLING"))
        self.assertEqual(clinical.outstanding_for_case(self.case), 0)
        record = clinical.sign_in(case=self.case, staff=self.surgeon)
        self.assertIsNotNone(record.sign_in_at)

    def test_emergency_needs_reason_when_unpaid(self):
        self._set_price("5000.00")
        case = services.book_case(patient=self.patient, procedure=self.procedure, category="EMERGENCY",
                                  booked_by=self.doctor)
        case.billing_encounter.charges.update(amount=Decimal("5000.00"), unit_price=Decimal("5000.00"))
        self.case = case
        self._ready_team_and_checks()
        with self.assertRaises(SurgeryError):
            clinical.sign_in(case=case, staff=self.surgeon, emergency_reason="too short")
        record = clinical.sign_in(case=case, staff=self.surgeon,
                                  emergency_reason="Ruptured viscus, no time to wait for payment")
        self.assertIn("Ruptured", record.emergency_start_reason)


class TeamTests(TheatreBase):
    def test_role_needs_matching_staff_role(self):
        with self.assertRaises(SurgeryError):
            clinical.add_team_member(case=self.case, staff_profile=self.nurse, role="SURGEON", added_by=self.doctor)

    def test_one_person_one_role(self):
        clinical.add_team_member(case=self.case, staff_profile=self.surgeon, role="SURGEON", added_by=self.doctor)
        with self.assertRaises(SurgeryError):
            clinical.add_team_member(case=self.case, staff_profile=self.surgeon, role="ANAESTHETIST",
                                     added_by=self.doctor)

    def test_one_per_role(self):
        clinical.add_team_member(case=self.case, staff_profile=self.surgeon, role="SURGEON", added_by=self.doctor)
        with self.assertRaises(SurgeryError):
            clinical.add_team_member(case=self.case, staff_profile=self.anaesthetist, role="SURGEON",
                                     added_by=self.doctor)


class SignInRuleTests(TheatreBase):
    def test_sign_in_needs_surgeon_and_anaesthetist(self):
        clinical.record_pre_op(case=self.case, staff=self.doctor, fasting_hours="8",
                               vitals={"pulse_bpm": "80", "systolic_mmhg": "120"})
        clinical.record_consent(case=self.case, staff=self.doctor, signer_name="Test Patient",
                                relationship="SELF", is_guardian=False, typed_signature="Test Patient")
        clinical.add_team_member(case=self.case, staff_profile=self.surgeon, role="SURGEON", added_by=self.doctor)
        with self.assertRaises(SurgeryError):
            clinical.sign_in(case=self.case, staff=self.surgeon)

    def test_sign_in_needs_pre_op_and_consent(self):
        self._team()
        with self.assertRaises(SurgeryError):
            clinical.sign_in(case=self.case, staff=self.surgeon)


class TheatreOrderTests(TheatreBase):
    def test_checkpoints_in_order(self):
        self._sign_in()
        with self.assertRaises(SurgeryError):
            clinical.incision(case=self.case, staff=self.surgeon)
        with self.assertRaises((SurgeryError, ValueError)):
            clinical.time_out(case=self.case, staff=self.surgeon, note="   ")
        clinical.time_out(case=self.case, staff=self.surgeon, note="Identity, site and allergies confirmed")
        with self.assertRaises(SurgeryError):
            clinical.closure(case=self.case, staff=self.surgeon)
        clinical.incision(case=self.case, staff=self.surgeon)

    def test_only_team_members_stamp(self):
        self._sign_in()
        outsider = make_staff("NURSE")
        with self.assertRaises(SurgeryError):
            clinical.time_out(case=self.case, staff=outsider, note="Identity, site and allergies confirmed")

    def test_sign_out_needs_closure(self):
        self._sign_in()
        clinical.time_out(case=self.case, staff=self.surgeon, note="Identity, site and allergies confirmed")
        clinical.incision(case=self.case, staff=self.surgeon)
        with self.assertRaises(SurgeryError):
            clinical.sign_out(case=self.case, staff=self.surgeon, note="Counts correct at sign-out")


class AnaestheticRecordTests(TheatreBase):
    def test_only_anaesthetist_records_and_not_before_sign_in(self):
        self._sign_in()
        t = _tz.now()
        with self.assertRaises(SurgeryError):
            clinical.record_anaesthetic(case=self.case, staff=self.surgeon, given_at=t, agent="Propofol",
                                        dose="100 mg", route="IV")
        with self.assertRaises(SurgeryError):
            clinical.record_anaesthetic(case=self.case, staff=self.anaesthetist,
                                        given_at=t - _td(days=1), agent="Propofol", dose="100 mg", route="IV")
        ev = clinical.record_anaesthetic(case=self.case, staff=self.anaesthetist, given_at=t,
                                         agent="Propofol", dose="100 mg", route="IV")
        self.assertEqual(ev.agent, "Propofol")


class RecoveryAndComplicationTests(TheatreBase):
    def test_recovery_only_after_sign_out(self):
        self._sign_in()
        with self.assertRaises(SurgeryError):
            clinical.record_recovery(case=self.case, staff=self.surgeon, observed_at=_tz.now(),
                                     vitals={"pulse_bpm": "80"})

    def test_recovery_and_validated_pain(self):
        self._through_sign_out()
        with self.assertRaises(ValueError):
            clinical.record_recovery(case=self.case, staff=self.surgeon, observed_at=_tz.now(),
                                     vitals={"pulse_bpm": "80"}, pain_score=11)
        obs = clinical.record_recovery(case=self.case, staff=self.surgeon, observed_at=_tz.now(),
                                       vitals={"pulse_bpm": "80"}, pain_score=3)
        self.assertEqual(obs.pain_score, 3)

    def test_complication_needs_severity_and_text(self):
        self._sign_in()
        with self.assertRaises(SurgeryError):
            clinical.record_complication(case=self.case, staff=self.surgeon, occurred_at=_tz.now(),
                                         severity="BIG", description="Bleeding during the procedure")
        with self.assertRaises(SurgeryError):
            clinical.record_complication(case=self.case, staff=self.surgeon, occurred_at=_tz.now(),
                                         severity="MINOR", description="short")
        item = clinical.record_complication(case=self.case, staff=self.surgeon, occurred_at=_tz.now(),
                                            severity="MODERATE", description="Bleeding controlled with diathermy")
        self.assertEqual(item.severity, "MODERATE")


class SignOffTests(TheatreBase):
    def test_sign_off_needs_sign_out_and_correct_roles(self):
        self._sign_in()
        with self.assertRaises(SurgeryError):
            clinical.surgeon_sign_off(case=self.case, staff=self.surgeon)

    def test_both_sign_completes_case(self):
        self._through_sign_out()
        with self.assertRaises(SurgeryError):
            clinical.anaesthetist_sign_off(case=self.case, staff=self.surgeon)
        clinical.surgeon_sign_off(case=self.case, staff=self.surgeon)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, "BOOKED")
        clinical.anaesthetist_sign_off(case=self.case, staff=self.anaesthetist)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, "COMPLETED")
        with self.assertRaises(SurgeryError):
            clinical.surgeon_sign_off(case=self.case, staff=self.surgeon)

    def test_completed_case_refuses_theatre_checkpoints_but_keeps_late_complications(self):
        self._through_sign_out()
        clinical.surgeon_sign_off(case=self.case, staff=self.surgeon)
        clinical.anaesthetist_sign_off(case=self.case, staff=self.anaesthetist)
        with self.assertRaises(SurgeryError):
            clinical.record_anaesthetic(case=self.case, staff=self.anaesthetist, given_at=_tz.now(),
                                        agent="Propofol", dose="100 mg", route="IV")
        item = clinical.record_complication(case=self.case, staff=self.surgeon, occurred_at=_tz.now(),
                                            severity="MINOR", description="Wound seepage found on the ward")
        self.assertEqual(item.severity, "MINOR")


class StockTests(TheatreBase):
    def setUp(self):
        super().setUp()
        self.item = TheatreStockItem.objects.create(name="Suture 2-0", unit="pack")
        self.pharm = make_staff("PHARMACIST")
        self.soon = _tz.localdate() + _td(days=30)
        self.later = _tz.localdate() + _td(days=300)

    def test_receive_refuses_expired_and_duplicate(self):
        with self.assertRaises(SurgeryError):
            clinical.receive_stock(item=self.item, batch_number="B1", expiry_date=_tz.localdate() - _td(days=1),
                                   quantity=5, staff=self.pharm)
        clinical.receive_stock(item=self.item, batch_number="B1", expiry_date=self.soon, quantity=5, staff=self.pharm)
        with self.assertRaises(SurgeryError):
            clinical.receive_stock(item=self.item, batch_number="B1", expiry_date=self.soon, quantity=5,
                                   staff=self.pharm)

    def test_use_takes_earliest_expiry_first_and_is_limited(self):
        clinical.receive_stock(item=self.item, batch_number="LATE", expiry_date=self.later, quantity=10, staff=self.pharm)
        clinical.receive_stock(item=self.item, batch_number="SOON", expiry_date=self.soon, quantity=3, staff=self.pharm)
        self._sign_in()
        uses = clinical.use_stock(case=self.case, item=self.item, quantity=5, staff=self.nurse)
        self.assertEqual([u.batch.batch_number for u in uses], ["SOON", "LATE"])
        self.assertEqual(TheatreStockBatch.objects.get(batch_number="SOON").quantity_on_hand, 0)
        self.assertEqual(TheatreStockBatch.objects.get(batch_number="LATE").quantity_on_hand, 8)
        with self.assertRaises(SurgeryError):
            clinical.use_stock(case=self.case, item=self.item, quantity=100, staff=self.nurse)


class StockPermissionTests(TestCase):
    def test_pharmacist_receives_nurse_uses_surgeon_signs(self):
        from hospital_system.permissions import has_any
        pharm, nurse, doc = make_staff("PHARMACIST"), make_staff("NURSE"), make_staff("DOCTOR")
        self.assertTrue(has_any(pharm.user, Cap.SURGERY_STOCK))
        self.assertTrue(has_any(nurse.user, Cap.SURGERY_STOCK))
        self.assertTrue(has_any(doc.user, Cap.SURGERY_SIGN_OFF))
        self.assertFalse(has_any(nurse.user, Cap.SURGERY_SIGN_OFF))
        self.assertFalse(has_any(pharm.user, Cap.SURGERY_CLINICAL))
        self.assertNotIn(Cap.SURGERY_SIGN_OFF, caps_for(make_staff("ADMIN").user))
        self.assertNotIn(Cap.SURGERY_CLINICAL, caps_for(make_staff("ADMIN").user))


class SurgeryViewTests(TheatreBase):
    """Pages render and actions go through the same rules as the services."""

    def setUp(self):
        super().setUp()
        from django.test import Client
        self.client_doc = Client()
        self.client_doc.force_login(self.doctor.user)
        self.client_nurse = Client()
        self.client_nurse.force_login(make_staff("NURSE").user)
        self.client_billing = Client()
        self.client_billing.force_login(make_staff("BILLING").user)

    def test_case_page_renders_for_each_role_that_can_view(self):
        self._through_sign_out()
        for client in (self.client_doc, self.client_nurse):
            resp = client.get(f"/surgery/case/{self.case.pk}/")
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "Sign-off")

    def test_billing_can_view_but_not_act(self):
        resp = self.client_billing.get(f"/surgery/case/{self.case.pk}/")
        self.assertEqual(resp.status_code, 200)
        resp = self.client_billing.post(f"/surgery/case/{self.case.pk}/do/pre_op/",
                                        {"fasting_hours": "8", "pulse_bpm": "80", "systolic_mmhg": "120"})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(PreOpCheck.objects.exists())

    def test_pre_op_post_saves_and_refusal_shows_message(self):
        resp = self.client_doc.post(f"/surgery/case/{self.case.pk}/do/pre_op/",
                                    {"fasting_hours": "2", "pulse_bpm": "80", "systolic_mmhg": "120"}, follow=True)
        self.assertContains(resp, "at least 6 hours")
        self.assertFalse(PreOpCheck.objects.exists())
        self.client_doc.post(f"/surgery/case/{self.case.pk}/do/pre_op/",
                             {"fasting_hours": "8", "pulse_bpm": "80", "systolic_mmhg": "120"})
        self.assertTrue(PreOpCheck.objects.filter(case=self.case).exists())

    def test_unknown_action_refused(self):
        resp = self.client_doc.post(f"/surgery/case/{self.case.pk}/do/nonsense/")
        self.assertEqual(resp.status_code, 302)

    def test_get_on_action_is_not_allowed(self):
        resp = self.client_doc.get(f"/surgery/case/{self.case.pk}/do/pre_op/")
        self.assertEqual(resp.status_code, 405)

    def test_booking_page_and_stock_page_render(self):
        self.assertEqual(self.client_doc.get(f"/surgery/book/{self.patient.pk}/").status_code, 200)
        self.assertEqual(self.client_nurse.get("/surgery/stock/").status_code, 200)
        self.assertEqual(self.client_billing.get("/surgery/stock/").status_code, 403)


class PatientFileEntryTests(TheatreBase):
    """The patient file links to booking, and the booking page explains when a visit is missing."""

    def test_doctor_sees_book_surgery_on_patient_file(self):
        from django.test import Client
        c = Client()
        c.force_login(self.doctor.user)
        resp = c.get(f"/patients/{self.patient.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"/surgery/book/{self.patient.pk}/")

    def test_nurse_does_not_see_book_surgery(self):
        from django.test import Client
        c = Client()
        c.force_login(make_staff("NURSE").user)
        resp = c.get(f"/patients/{self.patient.pk}/")
        self.assertNotContains(resp, f"/surgery/book/{self.patient.pk}/")

    def test_booking_page_warns_when_no_visit(self):
        from django.test import Client
        fresh = make_patient(self.doctor, sex="M")
        c = Client()
        c.force_login(self.doctor.user)
        resp = c.get(f"/surgery/book/{fresh.pk}/")
        self.assertContains(resp, "has no visit yet")
        self.assertContains(resp, f"/triage/encounter/new/{fresh.pk}/")
