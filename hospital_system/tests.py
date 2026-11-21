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
