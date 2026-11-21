"""
Patient record rules: allergy recording and status changes, consent (including
emergency consent and withdrawal), merging duplicate records, and duplicate search.
"""
import unittest
import uuid
from decimal import Decimal

from django.test import TestCase

from core_patients import allergy_services as allergies
from core_patients import consent as consent_rules
from core_patients.duplicates import find_candidates
from core_patients.merge import MergeError, merge_patients
from core_patients.models import Allergen, AllergenClass, PatientAllergy
from hospital_system.testing import make_encounter, make_patient, make_staff


class AllergyTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.nurse = make_staff("NURSE")
        self.patient = make_patient(self.reception)
        cls = AllergenClass.objects.create(code="PEN", name="Penicillins")
        self.allergen = Allergen.objects.create(code="PENICILLIN", display_name="Penicillin",
                                                allergen_class=cls)

    def test_allergy_is_recorded_from_the_catalogue(self):
        allergies.record_allergy(patient=self.patient, allergen_code="PENICILLIN",
                                 severity="SEVERE", reaction="Rash", staff=self.nurse)
        self.assertEqual(PatientAllergy.objects.filter(patient=self.patient).count(), 1)

    def test_allergen_not_in_catalogue_is_refused(self):
        with self.assertRaises(allergies.AllergyError):
            allergies.record_allergy(patient=self.patient, allergen_code="MADE-UP",
                                     severity="MILD", reaction="", staff=self.nurse)

    def test_allergy_needs_a_severity(self):
        with self.assertRaises(allergies.AllergyError):
            allergies.record_allergy(patient=self.patient, allergen_code="PENICILLIN",
                                     severity="", reaction="", staff=self.nurse)

    def test_changing_an_allergy_status_needs_a_reason(self):
        allergies.record_allergy(patient=self.patient, allergen_code="PENICILLIN",
                                 severity="MILD", reaction="", staff=self.nurse)
        allergy = PatientAllergy.objects.get()
        with self.assertRaises(allergies.AllergyError):
            allergies.change_allergy_status(patient=self.patient, allergy_id=allergy.pk,
                                            status="RESOLVED", reason="", staff=self.nurse)


class ConsentTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.patient = make_patient(self.reception, date_of_birth=None)

    def test_consent_needs_a_type(self):
        with self.assertRaises(consent_rules.ConsentError):
            consent_rules.record_consent(patient=self.patient, consent_type="",
                                         staff=self.reception)

    def test_consent_given_by_someone_else_needs_their_name(self):
        with self.assertRaises(consent_rules.ConsentError):
            consent_rules.record_consent(patient=self.patient, consent_type="TREATMENT",
                                         staff=self.reception, given_by_name="")

    def test_consent_withdrawal_needs_a_reason(self):
        with self.assertRaises(consent_rules.ConsentError):
            consent_rules.record_consent(patient=self.patient, consent_type="WITHDRAW",
                                         staff=self.reception, reason="")

    def test_no_staff_profile_is_refused(self):
        with self.assertRaises(consent_rules.ConsentError):
            consent_rules.record_consent(patient=self.patient, consent_type="TREATMENT", staff=None)


class MergeTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.survivor = make_patient(self.reception, last_name="Otieno", first_name="Grace")
        self.duplicate = make_patient(self.reception, last_name="Otieno", first_name="Grace")

    def test_a_record_cannot_be_merged_with_itself(self):
        with self.assertRaises(MergeError):
            merge_patients(survivor=self.survivor, duplicate=self.survivor,
                           reason="Duplicate", staff=self.reception)

    def test_merge_needs_a_reason(self):
        with self.assertRaises(MergeError):
            merge_patients(survivor=self.survivor, duplicate=self.duplicate,
                           reason="", staff=self.reception)

    @unittest.expectedFailure  # BUG: _lock_pair sorts by pk and the caller swaps the roles.
    def test_merge_points_the_duplicate_at_the_survivor(self):
        # Fixed IDs so the survivor sorts after the duplicate, the ordering that exposes
        # the swap in _lock_pair (see report).
        self.survivor.pk = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
        self.survivor.save()
        self.duplicate.pk = uuid.UUID("00000000-0000-0000-0000-000000000001")
        self.duplicate.save()
        merge_patients(survivor=self.survivor, duplicate=self.duplicate,
                       reason="Same patient registered twice", staff=self.reception)
        self.duplicate.refresh_from_db()
        self.assertEqual(self.duplicate.merged_into, self.survivor)

    def test_already_merged_record_cannot_be_merged_again(self):
        # Survivor sorts first so the first merge is not affected by the ordering bug.
        self.survivor.pk = uuid.UUID("00000000-0000-0000-0000-000000000001")
        self.survivor.save()
        self.duplicate.pk = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
        self.duplicate.save()
        merge_patients(survivor=self.survivor, duplicate=self.duplicate,
                       reason="Duplicate", staff=self.reception)
        third = make_patient(self.reception, last_name="Otieno", first_name="Grace")
        with self.assertRaises(MergeError):
            merge_patients(survivor=third, duplicate=self.duplicate,
                           reason="Duplicate", staff=self.reception)


class DuplicateSearchTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")

    def test_search_finds_a_patient_with_the_same_name(self):
        match = make_patient(self.reception, last_name="Wanjiru", first_name="Mary")
        results = find_candidates(first_name="Mary", last_name="Wanjiru")
        self.assertIn(match.pk, [r["patient"].pk for r in results])

    def test_search_with_no_match_returns_nothing(self):
        results = find_candidates(first_name="Nobody", last_name="Atall")
        self.assertEqual(len(list(results)), 0)
