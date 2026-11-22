"""Server-side validation rules. These call the services and helpers directly, not the forms."""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from hospital_system import validation as v
from triage_queue.intake import validate_triage_vitals


class DateOfBirthRules(SimpleTestCase):
    def test_year_1200_is_refused(self):
        with self.assertRaises(v.ValidationError):
            v.birth_date(date(1200, 5, 1))

    def test_future_birth_date_is_refused(self):
        with self.assertRaises(v.ValidationError):
            v.birth_date(date(2999, 1, 1))

    def test_ordinary_birth_date_is_accepted(self):
        self.assertEqual(v.birth_date(date(1990, 6, 15)), date(1990, 6, 15))

    def test_text_date_is_parsed_and_checked(self):
        with self.assertRaises(v.ValidationError):
            v.birth_date("1200-01-01")


class MoneyRules(SimpleTestCase):
    def test_negative_amount_is_refused(self):
        with self.assertRaises(v.ValidationError):
            v.money("-500", "Amount")

    def test_zero_refused_unless_allowed(self):
        with self.assertRaises(v.ValidationError):
            v.money("0", "Amount")
        self.assertEqual(v.money("0", "Amount", allow_zero=True), Decimal("0.00"))

    def test_amount_above_cap_is_refused(self):
        with self.assertRaises(v.ValidationError):
            v.money("99999999999", "Amount")

    def test_not_a_number_is_refused(self):
        for raw in ("NaN", "Infinity", "abc"):
            with self.assertRaises(v.ValidationError, msg=raw):
                v.money(raw, "Amount")


class TriageVitalRules(SimpleTestCase):
    def test_negative_temperature_is_refused(self):
        with self.assertRaises(v.ValidationError):
            validate_triage_vitals({"temperature_c": "-5"})

    def test_impossible_pulse_is_refused(self):
        with self.assertRaises(v.ValidationError):
            validate_triage_vitals({"pulse_bpm": "900"})

    def test_diastolic_must_be_below_systolic(self):
        with self.assertRaises(v.ValidationError):
            validate_triage_vitals({"systolic_mmhg": "120", "diastolic_mmhg": "130"})

    def test_text_in_numeric_field_is_refused(self):
        with self.assertRaises(v.ValidationError):
            validate_triage_vitals({"pulse_bpm": "abc"})

    def test_blank_vitals_are_allowed(self):
        clean = validate_triage_vitals({"pulse_bpm": "", "temperature_c": None})
        self.assertIsNone(clean["pulse_bpm"])

    def test_plausible_vitals_are_accepted(self):
        clean = validate_triage_vitals({"pulse_bpm": "72", "temperature_c": "36.8",
                                        "systolic_mmhg": "120", "diastolic_mmhg": "80"})
        self.assertEqual(clean["pulse_bpm"], 72)


class TextLengthRules(SimpleTestCase):
    def test_overlong_text_is_refused_not_cut(self):
        with self.assertRaises(v.ValidationError):
            v.text("x" * 256, "Result", max_length=255)

    def test_required_text_refuses_blank(self):
        with self.assertRaises(v.ValidationError):
            v.text("   ", "Batch", max_length=40, required=True)

    def test_text_is_trimmed_and_accepted(self):
        self.assertEqual(v.text("  ok  ", "Note", max_length=10), "ok")


class PregnancyDateRules(TestCase):
    def setUp(self):
        from hospital_system.testing import make_patient, make_staff
        self.staff = make_staff("NURSE")
        self.patient = make_patient(self.staff, sex="F")

    def _open(self, **kw):
        from maternity import services as m
        args = dict(patient=self.patient, lmp=None, edd=None, edd_override_reason="", staff=self.staff)
        args.update(kw)
        return m.open_pregnancy(**args)

    def test_lmp_in_1200_is_refused(self):
        from datetime import date
        from maternity.services import MaternityError
        with self.assertRaises(MaternityError):
            self._open(lmp=date(1200, 1, 1))

    def test_lmp_years_ago_is_refused(self):
        from datetime import timedelta
        from django.utils import timezone
        from maternity.services import MaternityError
        with self.assertRaises(MaternityError):
            self._open(lmp=timezone.localdate() - timedelta(days=400))

    def test_due_date_far_in_future_is_refused(self):
        from datetime import timedelta
        from django.utils import timezone
        from maternity.services import MaternityError
        with self.assertRaises(MaternityError):
            self._open(edd=timezone.localdate() + timedelta(days=900))
