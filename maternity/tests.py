"""
Maternity rules: pregnancy and birth only for non-male patients, due date from the
last period (+280 days) unless overridden with a reason, one ongoing pregnancy at a
time, birth measurements within range, and the vaccine schedule.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from django.utils import timezone as _tz  # noqa
from billing.models import Charge
from core_patients.models import Patient, PatientRelationship
from triage_queue.models import Encounter
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


# ---------- labour, births, fathers, sign-off, names, wards, report ----------

from decimal import Decimal  # noqa: E402

from billing import services as bill  # noqa: E402
from core_patients.models import Department  # noqa: E402
from maternity.models import DeliveryEpisode, LabourBed, LabourStay, NameCorrection  # noqa: E402
from maternity.report import build_after_birth_report  # noqa: E402
from wards.models import Bed, Ward  # noqa: E402

LONG = ("Post-partum haemorrhage controlled, mother stable, needs ward observation and "
        "monitoring of blood pressure and bleeding for the next several days")

def _baby(**over):
    base = dict(sex="F", first_name="", last_name="", birth_weight_g=3200, apgar_1=8, apgar_5=9,
                gestation_weeks=39, notes="", heart_rate_bpm=140, respiratory_rate=42,
                temperature_c=Decimal("36.8"), spo2_percent=98, bp_systolic=70, bp_diastolic=45,
                birth_length_cm=Decimal("50.0"), head_circumference_cm=Decimal("34.5"))
    base.update(over)
    return base


class LabourAdmissionTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.nurse = make_staff("NURSE")
        self.mother = make_patient(self.reception, sex="F")

    def test_labour_opens_without_any_queue_or_triage(self):
        stay = mat.open_labour(patient=self.mother, staff=self.nurse)
        self.assertEqual(stay.encounter.encounter_type, "MATERNITY")
        self.assertEqual(stay.encounter.status, "OPEN")
        self.assertFalse(stay.encounter.queue_entries.exists())

    def test_labour_bed_is_created_when_none_exists(self):
        self.assertEqual(LabourBed.objects.count(), 0)
        stay = mat.open_labour(patient=self.mother, staff=self.nurse)
        self.assertEqual(LabourBed.objects.count(), 1)
        self.assertEqual(stay.labour_bed.status, "OCCUPIED")

    def test_a_second_woman_gets_a_second_labour_bed_when_the_first_is_taken(self):
        stay1 = mat.open_labour(patient=self.mother, staff=self.nurse)
        other = make_patient(self.reception, sex="F")
        stay2 = mat.open_labour(patient=other, staff=self.nurse)
        self.assertEqual(LabourBed.objects.count(), 2)
        self.assertNotEqual(stay2.labour_bed_id, stay1.labour_bed_id)

    def test_labour_beds_are_separate_from_ward_beds(self):
        dept = Department.objects.create(name="Medicine", code="MED", kind="WARD")
        ward = Ward.objects.create(name="A", department=dept, bed_day_rate=Decimal("2000"))
        Bed.objects.create(ward=ward, code="A1", status="VACANT")
        mat.open_labour(patient=self.mother, staff=self.nurse)
        self.assertEqual(Bed.objects.get(code="A1").status, "VACANT")

    def test_a_woman_cannot_have_two_open_labour_records(self):
        mat.open_labour(patient=self.mother, staff=self.nurse)
        with self.assertRaises(mat.MaternityError):
            mat.open_labour(patient=self.mother, staff=self.nurse)

    def test_men_cannot_be_admitted_to_labour(self):
        man = make_patient(self.reception, sex="M")
        with self.assertRaises(mat.MaternityError):
            mat.open_labour(patient=man, staff=self.nurse)

    def test_delivered_before_arrival_is_recorded(self):
        stay = mat.open_labour(patient=self.mother, staff=self.nurse,
                               delivered_before_arrival=True, arrival_note="Delivered at home")
        self.assertTrue(stay.delivered_before_arrival)
        self.assertEqual(stay.arrival_note, "Delivered at home")


class BirthRecordingTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.nurse = make_staff("NURSE")
        self.doctor = make_staff("DOCTOR")
        self.mother = make_patient(self.reception, sex="F", last_name="Wanjiru")
        self.stay = mat.open_labour(patient=self.mother, staff=self.nurse)
        self.enc = self.stay.encounter
        self.when = timezone.now() - timedelta(hours=1)

    def birth(self, babies, **over):
        args = dict(mother_encounter=self.enc, staff=self.nurse, delivered_at=self.when,
                    delivery_type="SVD", babies=babies)
        args.update(over)
        return mat.record_birth_episode(**args)

    def test_birth_with_no_queue_is_recorded_on_the_labour_visit(self):
        episode = self.birth([_baby(first_name="Amina")])
        self.assertEqual(episode.babies.count(), 1)
        self.assertEqual(episode.labour_stay, self.stay)

    def test_twins_are_two_baby_records_under_one_episode(self):
        episode = self.birth([_baby(first_name="Amina"), _baby(sex="M", first_name="Brian")])
        self.assertEqual(episode.babies.count(), 2)
        self.assertEqual(Patient.objects.filter(birth_record__episode=episode).count(), 2)

    def test_triplets_are_three_records(self):
        episode = self.birth([_baby(first_name=n) for n in ("A", "B", "C")])
        self.assertEqual(episode.babies.count(), 3)

    def test_twins_still_produce_one_delivery_package_bill_for_the_mother(self):
        self.birth([_baby(first_name="A"), _baby(first_name="B")])
        packages = Charge.objects.filter(encounter=self.enc, description__startswith="Delivery package")
        self.assertEqual(packages.count(), 1)

    def test_each_baby_has_its_own_nursery_visit(self):
        self.birth([_baby(first_name="A"), _baby(first_name="B")])
        self.assertEqual(Encounter.objects.filter(encounter_type="CHILD_HEALTH").count(), 2)

    def test_surname_defaults_to_the_mother_without_father_details(self):
        self.birth([_baby()])
        baby = Patient.objects.filter(birth_record__isnull=False).first()
        self.assertEqual(baby.last_name, "Wanjiru")

    def test_surname_is_the_fathers_when_he_consents(self):
        self.birth([_baby()], father={"first_name": "Peter", "last_name": "Otieno", "consents": True})
        baby = Patient.objects.filter(birth_record__isnull=False).first()
        self.assertEqual(baby.last_name, "Otieno")
        father = PatientRelationship.objects.get(patient=baby, relationship="FATHER").related_patient
        self.assertEqual(father.first_name, "Peter")

    def test_father_details_without_consent_are_refused(self):
        with self.assertRaises(mat.MaternityError):
            self.birth([_baby()], father={"first_name": "Peter", "last_name": "Otieno", "consents": False})

    def test_temporary_name_is_flagged_when_no_first_name_given(self):
        self.birth([_baby(first_name="")])
        baby = Patient.objects.filter(birth_record__isnull=False).first()
        self.assertTrue(baby.is_temporary_name)

    def test_named_baby_is_not_temporary(self):
        self.birth([_baby(first_name="Amina")])
        baby = Patient.objects.filter(birth_record__isnull=False).first()
        self.assertFalse(baby.is_temporary_name)

    def test_vitals_out_of_range_are_refused(self):
        with self.assertRaises(mat.MaternityError):
            self.birth([_baby(heart_rate_bpm=400)])
        with self.assertRaises(mat.MaternityError):
            self.birth([_baby(spo2_percent=120)])

    def test_vitals_are_stored_on_the_baby_record(self):
        episode = self.birth([_baby(first_name="A")])
        d = episode.babies.get()
        self.assertEqual(d.heart_rate_bpm, 140)
        self.assertEqual(d.spo2_percent, 98)
        self.assertEqual(d.bp_systolic, 70)



class SignOffTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.nurse = make_staff("NURSE")
        self.doctor = make_staff("DOCTOR")
        self.mother = make_patient(self.reception, sex="F")
        self.stay = mat.open_labour(patient=self.mother, staff=self.nurse)
        self.episode = mat.record_birth_episode(
            mother_encounter=self.stay.encounter, staff=self.nurse,
            delivered_at=timezone.now() - timedelta(hours=1), delivery_type="SVD",
            babies=[_baby(first_name="A")])

    def test_a_nurse_or_midwife_cannot_sign_off(self):
        with self.assertRaises(mat.MaternityError):
            mat.sign_off_episode(episode=self.episode, doctor=self.nurse, note="Fine")

    def test_a_doctor_signs_off_with_a_note(self):
        mat.sign_off_episode(episode=self.episode, doctor=self.doctor, note="Normal delivery, mother and baby well.")
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, "SIGNED")
        self.assertEqual(self.episode.signed_off_by, self.doctor)

    def test_sign_off_needs_a_note(self):
        with self.assertRaises(mat.MaternityError):
            mat.sign_off_episode(episode=self.episode, doctor=self.doctor, note="  ")

    def test_an_episode_cannot_be_signed_twice(self):
        mat.sign_off_episode(episode=self.episode, doctor=self.doctor, note="Well")
        with self.assertRaises(mat.MaternityError):
            mat.sign_off_episode(episode=self.episode, doctor=self.doctor, note="Again")


class NameCorrectionTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.nurse = make_staff("NURSE")
        self.nurse2 = make_staff("NURSE")
        self.doctor = make_staff("DOCTOR")
        self.admin = make_staff("ADMIN")
        self.mother = make_patient(self.reception, sex="F")
        stay = mat.open_labour(patient=self.mother, staff=self.nurse)
        mat.record_birth_episode(mother_encounter=stay.encounter, staff=self.nurse,
                                 delivered_at=timezone.now() - timedelta(hours=1),
                                 delivery_type="SVD", babies=[_baby(first_name="")])
        self.baby = Patient.objects.filter(birth_record__isnull=False).first()

    def test_only_a_temporary_name_can_be_corrected_this_way(self):
        named = make_patient(self.reception, first_name="Grace")
        with self.assertRaises(mat.MaternityError):
            mat.request_name_correction(patient=named, first_name="X", last_name="Y",
                                        reason="typo", staff=self.nurse)

    def test_correction_waits_for_confirmation(self):
        mat.request_name_correction(patient=self.baby, first_name="Amina", last_name="Wanjiru",
                                    reason="Named by parents", staff=self.nurse)
        self.baby.refresh_from_db()
        self.assertTrue(self.baby.is_temporary_name)

    def test_the_requester_cannot_confirm_their_own_correction(self):
        c = mat.request_name_correction(patient=self.baby, first_name="Amina", last_name="W",
                                        reason="Named", staff=self.doctor)
        with self.assertRaises(mat.MaternityError):
            mat.confirm_name_correction(correction=c, confirmer=self.doctor)

    def test_a_nurse_cannot_confirm_a_correction(self):
        c = mat.request_name_correction(patient=self.baby, first_name="Amina", last_name="W",
                                        reason="Named", staff=self.nurse)
        with self.assertRaises(mat.MaternityError):
            mat.confirm_name_correction(correction=c, confirmer=self.nurse2)

    def test_a_doctor_confirms_and_the_name_changes(self):
        c = mat.request_name_correction(patient=self.baby, first_name="Amina", last_name="Wanjiru",
                                        reason="Named", staff=self.nurse)
        mat.confirm_name_correction(correction=c, confirmer=self.doctor)
        self.baby.refresh_from_db()
        self.assertEqual(self.baby.first_name, "Amina")
        self.assertFalse(self.baby.is_temporary_name)
        self.assertEqual(NameCorrection.objects.get().status, "CONFIRMED")

    def test_a_system_admin_can_confirm(self):
        c = mat.request_name_correction(patient=self.baby, first_name="Amina", last_name="Wanjiru",
                                        reason="Named", staff=self.nurse)
        mat.confirm_name_correction(correction=c, confirmer=self.admin)
        self.baby.refresh_from_db()
        self.assertFalse(self.baby.is_temporary_name)

    def test_temporary_children_list_shows_only_unconfirmed_names(self):
        self.assertIn(self.baby, list(mat.temporary_children()))
        c = mat.request_name_correction(patient=self.baby, first_name="Amina", last_name="W",
                                        reason="Named", staff=self.nurse)
        mat.confirm_name_correction(correction=c, confirmer=self.doctor)
        self.assertNotIn(self.baby, list(mat.temporary_children()))


class WardMoveTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.nurse = make_staff("NURSE")
        self.doctor = make_staff("DOCTOR")
        self.billing_officer = make_staff("BILLING")
        self.mother = make_patient(self.reception, sex="F")
        self.stay = mat.open_labour(patient=self.mother, staff=self.nurse)
        dept = Department.objects.create(name="Obstetrics", code="OBS", kind="WARD")
        self.ward = Ward.objects.create(name="Postnatal", department=dept, bed_day_rate=Decimal("2500"))
        self.bed = Bed.objects.create(ward=self.ward, code="PN-1", status="VACANT")

    def test_moving_to_a_ward_frees_the_labour_bed_and_bills_the_ward_bed(self):
        mat.move_to_ward(encounter=self.stay.encounter, bed_id=self.bed.pk, planned_days=2,
                         reason=LONG, staff=self.doctor)
        self.stay.refresh_from_db()
        self.assertTrue(self.stay.moved_to_ward)
        self.assertEqual(self.stay.labour_bed.status, "CLEANING")
        self.bed.refresh_from_db()
        self.assertEqual(self.bed.status, "OCCUPIED")
        self.assertTrue(Charge.objects.filter(source_type="BED_DAY").exists())

    def test_only_a_doctor_can_move_a_patient_to_a_ward(self):
        with self.assertRaises(Exception):
            mat.move_to_ward(encounter=self.stay.encounter, bed_id=self.bed.pk, planned_days=2,
                             reason=LONG, staff=self.nurse)


class AfterBirthReportTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.nurse = make_staff("NURSE")
        self.doctor = make_staff("DOCTOR")
        self.mother = make_patient(self.reception, sex="F", last_name="Wanjiru")
        stay = mat.open_labour(patient=self.mother, staff=self.nurse)
        self.episode = mat.record_birth_episode(
            mother_encounter=stay.encounter, staff=self.nurse,
            delivered_at=timezone.now() - timedelta(hours=1), delivery_type="SVD",
            babies=[_baby(first_name="Amina"), _baby(first_name="Brian", sex="M")],
            father={"first_name": "Peter", "last_name": "Otieno", "consents": True})

    def test_report_is_a_pdf(self):
        pdf = build_after_birth_report(self.episode)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 2000)

    def test_report_is_a_draft_until_signed_off(self):
        pdf = build_after_birth_report(self.episode)
        self.assertIn(b"%PDF", pdf)
        mat.sign_off_episode(episode=self.episode, doctor=self.doctor, note="Well")
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, "SIGNED")
        self.assertTrue(build_after_birth_report(self.episode).startswith(b"%PDF"))


class MaternityPagesTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.nurse = make_staff("NURSE")
        self.mother = make_patient(self.reception, sex="F")
        stay = mat.open_labour(patient=self.mother, staff=self.nurse)
        self.episode = mat.record_birth_episode(
            mother_encounter=stay.encounter, staff=self.nurse,
            delivered_at=timezone.now() - timedelta(hours=1), delivery_type="SVD",
            babies=[_baby(first_name="")])
        self.client.force_login(self.nurse.user)

    def test_temporary_children_page_lists_the_child(self):
        response = self.client.get("/maternity/temporary-children/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TEMPORARY")

    def test_after_birth_report_downloads_as_pdf(self):
        response = self.client.get(f"/maternity/episode/{self.episode.pk}/after-birth-report/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))


class LabourScreensTests(TestCase):
    """The screens a nurse and a doctor actually use, end to end."""

    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.nurse = make_staff("NURSE")
        self.doctor = make_staff("DOCTOR")
        self.mother = make_patient(self.reception, sex="F", last_name="Wanjiru", first_name="Mary")
        self.client.force_login(self.nurse.user)

    def admit(self):
        return self.client.post(f"/maternity/patients/{self.mother.pk}/labour/admit/")

    def test_patient_page_shows_admit_to_labour_for_a_woman(self):
        response = self.client.get(f"/patients/{self.mother.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Admit to labour")

    def test_admit_button_opens_labour_and_goes_to_the_labour_record(self):
        response = self.admit()
        stay = LabourStay.objects.get(patient=self.mother)
        self.assertRedirects(response, f"/maternity/labour/{stay.pk}/", fetch_redirect_response=False)

    def test_admitting_twice_shows_an_error_not_a_second_record(self):
        self.admit()
        self.admit()
        self.assertEqual(LabourStay.objects.filter(patient=self.mother).count(), 1)

    def test_labour_board_lists_the_woman(self):
        self.admit()
        response = self.client.get("/maternity/labour/")
        self.assertContains(response, "Wanjiru")

    def test_birth_form_saves_twins_from_the_posted_blocks(self):
        self.admit()
        stay = LabourStay.objects.get(patient=self.mother)
        response = self.client.post(f"/maternity/labour/{stay.pk}/birth/", {
            "delivered_at": (timezone.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            "delivery_type": "SVD",
            "baby_sex": ["F", "M"],
            "baby_first_name": ["Amina", ""],
            "baby_last_name": ["", ""],
            "birth_weight_g": ["3100", "2900"],
            "apgar_1": ["8", "7"], "apgar_5": ["9", "9"], "gestation_weeks": ["39", "38"],
            "heart_rate_bpm": ["140", "150"], "respiratory_rate": ["42", "44"],
            "temperature_c": ["36.8", "37.0"], "spo2_percent": ["98", "97"],
            "bp_systolic": ["70", "68"], "bp_diastolic": ["45", "44"],
            "birth_length_cm": ["50", "48"], "head_circumference_cm": ["34", "33"],
            "notes": ["", ""],
        })
        self.assertEqual(response.status_code, 302)
        episode = DeliveryEpisode.objects.get()
        self.assertEqual(episode.babies.count(), 2)
        second = episode.babies.get(baby__is_temporary_name=True)
        self.assertEqual(second.heart_rate_bpm, 150)

    def test_nurse_sees_no_sign_off_button_but_doctor_does(self):
        self.admit()
        stay = LabourStay.objects.get(patient=self.mother)
        mat.record_birth_episode(mother_encounter=stay.encounter, staff=self.nurse,
                                 delivered_at=timezone.now() - timedelta(hours=1),
                                 delivery_type="SVD", babies=[_baby(first_name="A")])
        nurse_page = self.client.get(f"/maternity/labour/{stay.pk}/")
        self.assertNotContains(nurse_page, "Sign off this delivery")
        self.client.force_login(self.doctor.user)
        doctor_page = self.client.get(f"/maternity/labour/{stay.pk}/")
        self.assertContains(doctor_page, "Sign off this delivery")

    def test_doctor_signs_off_through_the_button(self):
        self.admit()
        stay = LabourStay.objects.get(patient=self.mother)
        episode = mat.record_birth_episode(mother_encounter=stay.encounter, staff=self.nurse,
                                           delivered_at=timezone.now() - timedelta(hours=1),
                                           delivery_type="SVD", babies=[_baby(first_name="A")])
        self.client.force_login(self.doctor.user)
        self.client.post(f"/maternity/episode/{episode.pk}/sign-off/", {"signoff_note": "Well"})
        episode.refresh_from_db()
        self.assertEqual(episode.status, "SIGNED")

    def test_nurse_cannot_sign_off_even_with_a_direct_post(self):
        self.admit()
        stay = LabourStay.objects.get(patient=self.mother)
        episode = mat.record_birth_episode(mother_encounter=stay.encounter, staff=self.nurse,
                                           delivered_at=timezone.now() - timedelta(hours=1),
                                           delivery_type="SVD", babies=[_baby(first_name="A")])
        self.client.post(f"/maternity/episode/{episode.pk}/sign-off/", {"signoff_note": "Well"})
        episode.refresh_from_db()
        self.assertEqual(episode.status, "PENDING")

    def test_move_to_ward_page_is_doctor_only(self):
        self.admit()
        stay = LabourStay.objects.get(patient=self.mother)
        self.assertContains(self.client.get(f"/maternity/labour/{stay.pk}/move-to-ward/"),
                            "Only a doctor can move a patient to a ward")
        self.client.force_login(self.doctor.user)
        self.assertContains(self.client.get(f"/maternity/labour/{stay.pk}/move-to-ward/"),
                            "Move to ward and bill the bed")

    def test_name_correction_request_then_doctor_confirms(self):
        stay = mat.open_labour(patient=self.mother, staff=self.nurse)
        mat.record_birth_episode(mother_encounter=stay.encounter, staff=self.nurse,
                                 delivered_at=timezone.now() - timedelta(hours=1),
                                 delivery_type="SVD", babies=[_baby(first_name="")])
        baby = Patient.objects.get(birth_record__isnull=False)
        self.client.post(f"/maternity/child/{baby.pk}/name-correction/",
                         {"first_name": "Amina", "last_name": "Wanjiru", "reason": "Named by parents"})
        correction = NameCorrection.objects.get()
        self.assertEqual(correction.status, "PENDING")
        self.client.force_login(self.doctor.user)
        self.assertContains(self.client.get("/maternity/name-corrections/"), "Confirm")
        self.client.post(f"/maternity/name-corrections/{correction.pk}/confirm/")
        correction.refresh_from_db()
        self.assertEqual(correction.status, "CONFIRMED")
        baby.refresh_from_db()
        self.assertEqual(baby.first_name, "Amina")


class MaternityHomeTests(TestCase):
    def test_maternity_home_and_name_pages_render(self):
        staff = make_staff("NURSE")
        self.client.force_login(staff.user)
        for url in ["/maternity/", "/maternity/temporary-children/", "/maternity/name-corrections/",
                    "/maternity/labour/"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
