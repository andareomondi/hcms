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
