"""
Maternity rules: pregnancy and birth only for non-male patients, due date from the
last period (+280 days) unless overridden with a reason, one ongoing pregnancy at a
time, birth measurements within range, and the vaccine schedule.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from hospital_system.testing import make_encounter, make_patient, make_staff
from maternity import services as mat
from maternity.models import Pregnancy


class PregnancyTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.doctor = make_staff("DOCTOR")
        self.mother = make_patient(self.reception, sex="F")
        self.lmp = timezone.localdate() - timedelta(days=100)

    def test_male_patient_cannot_have_a_pregnancy_recorded(self):
        man = make_patient(self.reception, sex="M")
        with self.assertRaises(mat.MaternityError):
            mat.open_pregnancy(patient=man, lmp=self.lmp, edd=None, edd_override_reason="",
                               staff=self.doctor)

    def test_due_date_is_calculated_as_last_period_plus_280_days(self):
        preg = mat.open_pregnancy(patient=self.mother, lmp=self.lmp, edd=None,
                                  edd_override_reason="", staff=self.doctor)
        self.assertEqual(preg.edd, self.lmp + timedelta(days=280))

    def test_last_period_in_the_future_is_refused(self):
        with self.assertRaises(mat.MaternityError):
            mat.open_pregnancy(patient=self.mother, lmp=timezone.localdate() + timedelta(days=1),
                               edd=None, edd_override_reason="", staff=self.doctor)

    def test_different_due_date_needs_a_reason(self):
        other = self.lmp + timedelta(days=200)
        with self.assertRaises(mat.MaternityError):
            mat.open_pregnancy(patient=self.mother, lmp=self.lmp, edd=other,
                               edd_override_reason="", staff=self.doctor)

    def test_second_ongoing_pregnancy_is_refused(self):
        mat.open_pregnancy(patient=self.mother, lmp=self.lmp, edd=None,
                           edd_override_reason="", staff=self.doctor)
        with self.assertRaises(mat.MaternityError):
            mat.open_pregnancy(patient=self.mother, lmp=self.lmp, edd=None,
                               edd_override_reason="", staff=self.doctor)

    def test_pregnancy_loss_needs_a_note_and_closes_the_pregnancy(self):
        preg = mat.open_pregnancy(patient=self.mother, lmp=self.lmp, edd=None,
                                  edd_override_reason="", staff=self.doctor)
        with self.assertRaises(mat.MaternityError):
            mat.close_pregnancy(pregnancy=preg, status="LOSS", note="", staff=self.doctor)
        mat.close_pregnancy(pregnancy=preg, status="LOSS", note="Early loss, mother stable",
                            staff=self.doctor)
        preg.refresh_from_db()
        self.assertNotEqual(preg.status, "ONGOING")


class DeliveryTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.doctor = make_staff("DOCTOR")
        self.mother = make_patient(self.reception, sex="F")
        self.encounter = make_encounter(self.mother, self.reception, encounter_type="MATERNITY")
        self.now = timezone.now() - timedelta(hours=1)

    def record(self, **over):
        args = dict(mother_encounter=self.encounter, staff=self.doctor, delivered_at=self.now,
                    delivery_type="SVD", baby_sex="F", birth_weight_g=3200, apgar_1=8,
                    apgar_5=9, gestation_weeks=39, notes="Uneventful", baby_first_name="",
                    baby_last_name="")
        args.update(over)
        return mat.record_delivery(**args)

    def test_birth_weight_below_200g_is_refused(self):
        with self.assertRaises(mat.MaternityError):
            self.record(birth_weight_g=150)

    def test_birth_weight_above_8000g_is_refused(self):
        with self.assertRaises(mat.MaternityError):
            self.record(birth_weight_g=9000)

    def test_apgar_out_of_range_is_refused(self):
        with self.assertRaises(mat.MaternityError):
            self.record(apgar_1=11)

    def test_birth_in_the_future_is_refused(self):
        with self.assertRaises(mat.MaternityError):
            self.record(delivered_at=timezone.now() + timedelta(hours=2))

    def test_delivery_type_is_required(self):
        with self.assertRaises(mat.MaternityError):
            self.record(delivery_type="")


class ChildAndVaccineTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.nurse = make_staff("NURSE")
        self.mother = make_patient(self.reception, sex="F")

    def test_child_needs_first_and_last_name(self):
        from datetime import date
        with self.assertRaises(mat.MaternityError):
            mat.register_child(parent=self.mother, first_name="", last_name="",
                               date_of_birth=date(2026, 11, 1), sex="F", id_type="NONE",
                               id_number="", dob_is_estimated=False, is_guarantor=True,
                               staff=self.reception)

    def test_child_date_of_birth_cannot_be_in_the_future(self):
        from datetime import date
        with self.assertRaises(mat.MaternityError):
            mat.register_child(parent=self.mother, first_name="Baby", last_name="Test",
                               date_of_birth=timezone.localdate() + timedelta(days=3), sex="F",
                               id_type="NONE", id_number="", dob_is_estimated=False,
                               is_guarantor=True, staff=self.reception)

    def test_patient_cannot_be_linked_to_themselves(self):
        with self.assertRaises(mat.MaternityError):
            mat.link_relationship(patient=self.mother, related=self.mother,
                                  relationship="PARENT", is_guarantor=False)

    def test_vaccine_needs_a_serial(self):
        from datetime import date
        child = make_patient(self.reception, date_of_birth=date(2026, 10, 1))
        with self.assertRaises(mat.MaternityError):
            mat.record_vaccination(patient=child, vaccine="BCG", given_at=timezone.now(),
                                   serial="", staff=self.nurse)
