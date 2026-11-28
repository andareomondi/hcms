"""Surgery wired into the admin, the data manager, the navigation, the dashboard and the overview."""
from decimal import Decimal

from django.contrib import admin
from django.test import Client, TestCase

from hospital_system.testing import make_patient, make_staff
from dashboard.admin_crud import MANAGED

from .models import SurgicalProcedure
from . import services
from triage_queue.models import Encounter


def _admin_client():
    user = make_staff("ADMIN").user
    user.is_superuser = True
    user.save()
    c = Client()
    c.force_login(user)
    return c


class ProcedureTabTests(TestCase):
    def setUp(self):
        self.c = _admin_client()

    def test_admin_adds_a_procedure(self):
        resp = self.c.post("/admin-master/surgery/procedures/new/", {
            "name": "Hernia repair", "category": "INTERMEDIATE", "price": "30000", "is_active": "on"}, follow=True)
        self.assertTrue(SurgicalProcedure.objects.filter(name="Hernia repair", is_active=True).exists())
        self.assertContains(resp, "added to the procedures")

    def test_bad_price_is_refused_with_a_message(self):
        self.c.post("/admin-master/surgery/procedures/new/", {
            "name": "Bad", "category": "MINOR", "price": "-5"})
        self.assertFalse(SurgicalProcedure.objects.filter(name="Bad").exists())

    def test_withdraw_and_offer_again(self):
        p = SurgicalProcedure.objects.create(name="Mole removal", category="MINOR", price=Decimal("2000"))
        self.c.post(f"/admin-master/surgery/procedures/{p.pk}/toggle/")
        p.refresh_from_db()
        self.assertFalse(p.is_active)
        self.c.post(f"/admin-master/surgery/procedures/{p.pk}/toggle/")
        p.refresh_from_db()
        self.assertTrue(p.is_active)

    def test_delete_only_without_cases(self):
        doctor = make_staff("DOCTOR")
        patient = make_patient(doctor, sex="F")
        procedure = SurgicalProcedure.objects.create(name="Tonsillectomy", category="INTERMEDIATE",
                                                     price=Decimal("0"))
        services.book_case(patient=patient, procedure=procedure, category="EMERGENCY", booked_by=doctor)
        self.c.post(f"/admin-master/surgery/procedures/{procedure.pk}/delete/")
        self.assertTrue(SurgicalProcedure.objects.filter(pk=procedure.pk).exists())
        empty = SurgicalProcedure.objects.create(name="Unused", category="MINOR", price=Decimal("1"))
        self.c.post(f"/admin-master/surgery/procedures/{empty.pk}/delete/")
        self.assertFalse(SurgicalProcedure.objects.filter(pk=empty.pk).exists())

    def test_admin_master_surgery_tab_renders(self):
        resp = self.c.get("/admin-master/?tab=surgery")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Procedures the hospital offers")

    def test_non_admin_cannot_manage_procedures(self):
        c = Client()
        c.force_login(make_staff("DOCTOR").user)
        resp = c.post("/admin-master/surgery/procedures/new/", {"name": "X", "category": "MINOR", "price": "1"})
        self.assertNotEqual(resp.status_code, 200)
        self.assertFalse(SurgicalProcedure.objects.filter(name="X").exists())


class DataManagerAndAdminTests(TestCase):
    def test_every_surgery_model_is_managed(self):
        from surgery import models as m
        surgery_models = [name for name in dir(m) if isinstance(getattr(m, name), type)
                          and getattr(m, name).__module__ == m.__name__
                          and hasattr(getattr(m, name), "_meta")]
        for name in surgery_models:
            self.assertIn(f"surgery.{name}", MANAGED, name)

    def test_clinical_records_are_read_only_in_the_data_manager(self):
        for key in ("surgery.SurgicalCase", "surgery.TheatreRecord", "surgery.SurgicalSignOff"):
            self.assertFalse(MANAGED[key].get("editable", True), key)
            self.assertFalse(MANAGED[key].get("deletable", True), key)

    def test_surgery_models_registered_in_admin(self):
        from surgery.models import SurgicalProcedure, SurgicalCase, TheatreStockItem
        for model in (SurgicalProcedure, SurgicalCase, TheatreStockItem):
            self.assertIn(model, admin.site._registry)


class SurgeryDashboardTests(TestCase):
    def setUp(self):
        self.doctor = make_staff("DOCTOR")
        self.patient = make_patient(self.doctor, sex="M", date_of_birth=None)
        self.procedure = SurgicalProcedure.objects.create(name="Appendicectomy", category="INTERMEDIATE",
                                                          price=Decimal("1000"))

    def test_dashboard_renders_with_nav_and_chart_data(self):
        c = Client()
        c.force_login(self.doctor.user)
        resp = c.get("/surgery/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Surgery dashboard")
        self.assertContains(resp, 'id="surgery-data"')

    def test_nurse_without_surgery_view_is_refused(self):
        c = Client()
        c.force_login(make_staff("RECEPTION").user)
        self.assertEqual(c.get("/surgery/").status_code, 403)

    def test_staff_overview_shows_surgery_chart_for_surgery_roles(self):
        c = Client()
        c.force_login(self.doctor.user)
        resp = c.get("/")
        self.assertContains(resp, 'id="c-surgery"')

    def test_staff_overview_hides_surgery_chart_from_reception(self):
        c = Client()
        c.force_login(make_staff("RECEPTION").user)
        resp = c.get("/")
        self.assertNotContains(resp, 'id="c-surgery"')

    def test_admin_overview_has_surgery_charts(self):
        c = _admin_client()
        resp = c.get("/admin-master/")
        self.assertContains(resp, 'id="c-surgery-daily"')
        self.assertContains(resp, 'id="c-surgery-severity"')
        self.assertContains(resp, "Surgeries this month")


class VitalsStorageTests(TestCase):
    """Decimal vitals (temperature) must survive the JSON field."""

    def test_pre_op_with_temperature_saves_and_reads_back(self):
        from .models import PreOpCheck
        doctor = make_staff("DOCTOR")
        patient = make_patient(doctor, sex="F")
        procedure = SurgicalProcedure.objects.create(name="Cyst removal", category="MINOR", price=Decimal("0"))
        case = services.book_case(patient=patient, procedure=procedure, category="EMERGENCY", booked_by=doctor)
        from . import clinical
        clinical.record_pre_op(case=case, staff=doctor, fasting_hours="8",
                               vitals={"temperature_c": "37.5", "pulse_bpm": "80", "systolic_mmhg": "120"})
        check = PreOpCheck.objects.get(case=case)
        self.assertEqual(check.vitals["temperature_c"], "37.5")
