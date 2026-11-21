"""
Permission matrix, taken from the role table in the README and checked against the code.
Each role can do its own work and not the work of other roles.
"""
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from hospital_system.permissions import ADMIN_CAPS, Cap, caps_for
from hospital_system.testing import make_staff


class PermissionMatrixTests(TestCase):
    def can(self, staff, cap):
        return cap in caps_for(staff.user)

    def test_anonymous_user_has_no_capabilities(self):
        self.assertEqual(caps_for(AnonymousUser()), set())

    def test_reception_cannot_take_payments_or_write_clinical_notes(self):
        r = make_staff("RECEPTION")
        self.assertFalse(self.can(r, Cap.TAKE_PAYMENT))
        self.assertFalse(self.can(r, Cap.CLINICAL_ACTIONS))

    def test_billing_officer_cannot_do_clinical_work(self):
        b = make_staff("BILLING")
        self.assertTrue(self.can(b, Cap.TAKE_PAYMENT))
        self.assertFalse(self.can(b, Cap.CLINICAL_ACTIONS))
        self.assertFalse(self.can(b, Cap.DISPENSE))

    def test_doctor_can_admit_and_order_but_cannot_take_payments(self):
        d = make_staff("DOCTOR")
        self.assertTrue(self.can(d, Cap.WARD_ACTIONS))
        self.assertTrue(self.can(d, Cap.CLINICAL_ACTIONS))
        self.assertFalse(self.can(d, Cap.TAKE_PAYMENT))

    def test_nurse_can_administer_but_cannot_prescribe_or_dispense(self):
        n = make_staff("NURSE")
        self.assertTrue(self.can(n, Cap.NURSE_ADMINISTER))
        self.assertFalse(self.can(n, Cap.CLINICAL_ACTIONS))
        self.assertFalse(self.can(n, Cap.DISPENSE))

    def test_pharmacist_dispenses_but_cannot_admit(self):
        p = make_staff("PHARMACIST")
        self.assertTrue(self.can(p, Cap.DISPENSE))
        self.assertFalse(self.can(p, Cap.WARD_ACTIONS))

    def test_administrator_has_no_clinical_or_clocking_capability(self):
        a = make_staff("ADMIN")
        for cap in (Cap.DISPENSE, Cap.CLINICAL_ACTIONS, Cap.NURSE_ADMINISTER,
                    Cap.RADIOLOGY_REPORT, Cap.DUTY_SELF):
            self.assertNotIn(cap, caps_for(a.user))
        self.assertEqual(caps_for(a.user), set(ADMIN_CAPS))

    def test_superuser_has_every_capability(self):
        from hospital_system.permissions import ALL_CAPS
        user = make_staff("RECEPTION").user
        user.is_superuser = True
        user.save()
        self.assertEqual(caps_for(user), set(ALL_CAPS))

    def test_user_without_a_staff_profile_has_no_capabilities(self):
        from django.contrib.auth import get_user_model
        bare = get_user_model().objects.create_user(username="bare", password="x-test-password-123")
        self.assertEqual(caps_for(bare), set())


class LogoutRuleTests(TestCase):
    """Logout is refused while a doctor still owns patients; it succeeds otherwise."""

    def setUp(self):
        from core_patients.models import Department
        from duty import services as duty
        from duty.models import Room
        from hospital_system.testing import make_encounter, make_patient
        from triage_queue.models import QueueEntry
        self.doc = make_staff("DOCTOR")
        self.reception = make_staff("RECEPTION")
        dept = Department.objects.create(name="Clinic", code="CLN", kind="CLINICAL")
        self.room = Room.objects.create(department=dept, code="RL", name="Room L")
        duty.clock_in(self.doc, self.room)
        self.patient = make_patient(self.reception)
        self.encounter = make_encounter(self.patient, self.reception)
        self.QueueEntry = QueueEntry
        self.client.force_login(self.doc.user)

    def test_logout_is_refused_while_a_patient_is_assigned(self):
        self.QueueEntry.objects.create(encounter=self.encounter, assigned_doctor=self.doc,
                                       status="WAITING", priority=1)
        response = self.client.post("/accounts/logout/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)  # still signed in
        from duty import services as duty
        self.assertIsNotNone(duty.current_session(self.doc))  # still on duty, not silently clocked out

    def test_logout_succeeds_and_clocks_out_when_nothing_is_open(self):
        from duty import services as duty
        response = self.client.post("/accounts/logout/")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertIsNone(duty.current_session(self.doc))

    def test_logout_is_refused_for_an_open_ward_stay_the_doctor_admitted(self):
        from wards.models import Admission, Bed, Ward
        from core_patients.models import Department
        from django.utils import timezone
        from hospital_system.testing import make_encounter
        dept2 = Department.objects.create(name="Med", code="MED", kind="WARD")
        ward = Ward.objects.create(name="A", department=dept2, bed_day_rate=100)
        bed = Bed.objects.create(ward=ward, code="A1", status="OCCUPIED")
        stay = make_encounter(self.patient, self.reception, encounter_type="WARD_STAY")
        Admission.objects.create(patient=self.patient, encounter=stay, bed=bed,
                                 admitted_at=timezone.now(), admitted_by=self.doc,
                                 planned_days=1, reason="x" * 100)
        self.client.post("/accounts/logout/")
        self.assertIn("_auth_user_id", self.client.session)
