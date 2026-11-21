"""Duty rules: clocking in and out, one room per person, room occupancy, and who is on duty."""
from django.test import TestCase

from core_patients.models import Department
from duty import services as duty
from duty.models import Room
from hospital_system.testing import make_staff


class DutyTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Clinic", code="CLN", kind="CLINICAL")
        self.room1 = Room.objects.create(department=self.dept, code="R1", name="Room 1")
        self.room2 = Room.objects.create(department=self.dept, code="R2", name="Room 2")
        self.doc = make_staff("DOCTOR")
        self.doc2 = make_staff("DOCTOR")

    def test_clock_in_starts_a_session_in_the_room(self):
        duty.clock_in(self.doc, self.room1)
        self.assertIsNotNone(duty.current_session(self.doc))

    def test_cannot_clock_in_twice(self):
        duty.clock_in(self.doc, self.room1)
        with self.assertRaises(duty.DutyError):
            duty.clock_in(self.doc, self.room2)

    def test_occupied_room_is_refused_for_a_second_person(self):
        duty.clock_in(self.doc, self.room1)
        with self.assertRaises(duty.DutyError):
            duty.clock_in(self.doc2, self.room1)

    def test_clock_out_when_not_clocked_in_is_refused(self):
        with self.assertRaises(duty.DutyError):
            duty.clock_out(self.doc)

    def test_clock_out_ends_the_session(self):
        duty.clock_in(self.doc, self.room1)
        duty.clock_out(self.doc)
        self.assertIsNone(duty.current_session(self.doc))

    def test_only_clocked_in_staff_are_on_duty_for_their_role(self):
        duty.clock_in(self.doc, self.room1)
        on_duty = list(duty.on_duty_staff(role="DOCTOR"))
        self.assertIn(self.doc, on_duty)
        self.assertNotIn(self.doc2, on_duty)

    def test_changing_room_requires_being_clocked_in(self):
        with self.assertRaises(duty.DutyError):
            duty.change_room(self.doc, self.room2)

    def test_changing_to_an_occupied_room_is_refused(self):
        duty.clock_in(self.doc, self.room1)
        duty.clock_in(self.doc2, self.room2)
        with self.assertRaises(duty.DutyError):
            duty.change_room(self.doc, self.room2)


class OpenCommitmentsTests(TestCase):
    """A doctor cannot stop duty while owning patients or open ward stays."""

    def setUp(self):
        from core_patients.models import Department
        from triage_queue.models import QueueEntry
        from hospital_system.testing import make_encounter, make_patient
        self.doc = make_staff("DOCTOR")
        self.reception = make_staff("RECEPTION")
        self.dept = Department.objects.create(name="Clinic", code="CLN", kind="CLINICAL")
        self.room = Room.objects.create(department=self.dept, code="R1", name="Room 1")
        self.patient = make_patient(self.reception)
        self.encounter = make_encounter(self.patient, self.reception)
        self.QueueEntry = QueueEntry
        duty.clock_in(self.doc, self.room)

    def assign(self, status):
        return self.QueueEntry.objects.create(encounter=self.encounter, assigned_doctor=self.doc,
                                              status=status, priority=1)

    def test_no_commitments_means_no_blockers(self):
        from duty.commitments import open_commitments
        self.assertEqual(open_commitments(self.doc), [])

    def test_waiting_patient_blocks_clock_out(self):
        self.assign("WAITING")
        with self.assertRaises(duty.DutyError):
            duty.clock_out(self.doc)
        self.assertIsNotNone(duty.current_session(self.doc))

    def test_every_open_status_blocks_clock_out(self):
        for status in ["AWAITING_PAYMENT", "IN_CONSULTATION", "AWAITING_RESULTS",
                       "IN_PROCEDURE", "DIAGNOSTICS", "ADMITTED"]:
            self.QueueEntry.objects.all().delete()
            self.assign(status)
            with self.subTest(status=status):
                with self.assertRaises(duty.DutyError):
                    duty.clock_out(self.doc)

    def test_discharged_patient_does_not_block(self):
        self.assign("DISCHARGED")
        duty.clock_out(self.doc)
        self.assertIsNone(duty.current_session(self.doc))

    def test_another_doctors_patients_do_not_block_this_doctor(self):
        other = make_staff("DOCTOR")
        self.QueueEntry.objects.create(encounter=self.encounter, assigned_doctor=other,
                                       status="WAITING", priority=1)
        from duty.commitments import open_commitments
        self.assertEqual(open_commitments(self.doc), [])

    def test_blocker_applies_even_when_not_clocked_in(self):
        from django.utils import timezone
        from duty.commitments import open_commitments
        self.assign("WAITING")
        duty.current_session(self.doc).__class__.objects.filter(staff=self.doc).update(
            clocked_out_at=timezone.now())
        self.assertIsNone(duty.current_session(self.doc))
        self.assertTrue(open_commitments(self.doc))

    def test_non_doctor_has_no_commitments(self):
        from duty.commitments import open_commitments
        self.assertEqual(open_commitments(self.reception), [])


class RequireOnDutyDoctorTests(TestCase):
    def test_doctor_clocked_in_passes(self):
        from duty.commitments import require_on_duty_doctor
        from core_patients.models import Department
        doc = make_staff("DOCTOR")
        dept = Department.objects.create(name="Clinic", code="CLN", kind="CLINICAL")
        room = Room.objects.create(department=dept, code="R9", name="Room 9")
        duty.clock_in(doc, room)
        require_on_duty_doctor(doc)  # must not raise

    def test_none_is_refused(self):
        from duty.commitments import require_on_duty_doctor
        with self.assertRaises(duty.DutyError):
            require_on_duty_doctor(None)
