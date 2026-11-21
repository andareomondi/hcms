"""Notifications: a person gets their own alerts; a role broadcast reaches only people on duty."""
from django.test import TestCase

from core_patients.models import Department
from duty import services as duty
from duty.models import Room
from hospital_system.testing import make_staff
from notifications import services as notif
from notifications.models import Notification


class NotificationTests(TestCase):
    def setUp(self):
        self.doc = make_staff("DOCTOR")
        self.other = make_staff("DOCTOR")

    def test_notify_creates_one_row_per_recipient(self):
        notif.notify([self.doc, self.other], kind="PATIENT_ASSIGNED", message="Patient ready")
        self.assertEqual(Notification.objects.count(), 2)

    def test_unread_count_is_only_for_that_person(self):
        notif.notify([self.doc], kind="SYSTEM", message="Hello")
        self.assertEqual(len(list(notif.unread_for(self.doc))), 1)
        self.assertEqual(len(list(notif.unread_for(self.other))), 0)

    def test_marking_read_clears_the_unread_list(self):
        notif.notify([self.doc], kind="SYSTEM", message="Hello")
        notif.mark_all_read(self.doc)
        self.assertEqual(len(list(notif.unread_for(self.doc))), 0)

    def test_role_broadcast_reaches_only_staff_clocked_in(self):
        dept = Department.objects.create(name="Clinic", code="CLN", kind="CLINICAL")
        room = Room.objects.create(department=dept, code="R1", name="Room 1")
        duty.clock_in(self.doc, room)
        notif.notify_on_duty(role="DOCTOR", kind="SYSTEM", message="Lab result ready")
        recipients = set(Notification.objects.values_list("recipient_id", flat=True))
        self.assertIn(self.doc.pk, recipients)
        self.assertNotIn(self.other.pk, recipients)
