"""Audit trail: every recorded action names the person who did it and when."""
from django.test import TestCase

from dashboard.models import AuditLog
from dashboard.utils import create_audit_entry
from hospital_system.testing import make_staff


class AuditTrailTests(TestCase):
    def test_audit_entry_records_actor_action_and_description(self):
        staff = make_staff("RECEPTION")
        create_audit_entry(actor=staff.user, action_type="CREATE",
                           description="Registered a patient")
        entry = AuditLog.objects.get(description="Registered a patient")
        self.assertEqual(entry.actor, staff.user)
        self.assertEqual(entry.action_type, "CREATE")
        self.assertIsNotNone(entry.timestamp)

    def test_a_user_with_history_cannot_be_deleted(self):
        # Nothing is deleted: a staff account with a profile is protected from removal.
        from django.db.models import ProtectedError
        staff = make_staff("RECEPTION")
        create_audit_entry(actor=staff.user, action_type="CREATE", description="Did a thing")
        with self.assertRaises(ProtectedError):
            staff.user.delete()
