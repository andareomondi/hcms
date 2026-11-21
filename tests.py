"""
Consultation rules: only the assigned doctor closes a consultation, a clinical note
is required, and no scan or lab order may still be open.
"""
from django.test import TestCase
from django.utils import timezone

from doctors_portal.models import ClinicalNote
from hospital_system.testing import make_encounter, make_patient, make_staff
from radiology.models import ScanOrder
from triage_queue import services as tq
from triage_queue.models import QueueEntry
from triage_queue.services import FlowError


class ConsultationCloseTests(TestCase):
    def setUp(self):
        self.reception = make_staff("RECEPTION")
        self.doctor = make_staff("DOCTOR")
        self.other_doctor = make_staff("DOCTOR")
        self.patient = make_patient(self.reception)
        self.encounter = make_encounter(self.patient, self.reception)
        self.entry = QueueEntry.objects.create(
            encounter=self.encounter, assigned_doctor=self.doctor, status="IN_CONSULTATION",
            priority=1)

    def note(self):
        return ClinicalNote.objects.create(
            encounter=self.encounter, author=self.doctor, presenting_complaint="Cough",
            examination="Chest clear", diagnosis="Viral URTI", plan="Fluids and rest")

    def test_consultation_cannot_close_without_a_clinical_note(self):
        with self.assertRaises(FlowError):
            tq.close_consultation(self.encounter, self.doctor)

    def test_only_the_assigned_doctor_can_close_the_consultation(self):
        self.note()
        with self.assertRaises(FlowError):
            tq.close_consultation(self.encounter, self.other_doctor)

    def test_closing_discharges_the_patient_and_closes_the_visit(self):
        self.note()
        result = tq.close_consultation(self.encounter, self.doctor)
        self.encounter.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertEqual(self.encounter.status, "CLOSED")
        self.assertEqual(self.entry.status, "DISCHARGED")
        self.assertEqual(result, {"admitted": False})

    def test_consultation_cannot_close_while_a_scan_is_outstanding(self):
        self.note()
        ScanOrder.objects.create(encounter=self.encounter, ordered_by=self.doctor,
                                 modality="X-Ray", body_part="Chest",
                                 clinical_indication="Cough", status="ORDERED")
        with self.assertRaises(FlowError):
            tq.close_consultation(self.encounter, self.doctor)

    def test_patient_not_in_consultation_cannot_be_closed(self):
        self.entry.status = "WAITING"
        self.entry.save()
        self.note()
        with self.assertRaises(FlowError):
            tq.close_consultation(self.encounter, self.doctor)
