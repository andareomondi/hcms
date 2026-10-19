"""
DEVELOPMENT ONLY. Operator console.

Sign in with a staff username and password, then perform the actions that account can use.
Admin accounts can do everything except delete records.
Every action is written to the audit log as DEV_OPERATOR.

Refuses to run unless DEBUG is True.
Usage: python dev_operator.py
"""
import os
import sys
import traceback
from getpass import getpass

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hospital_system.settings")
django.setup()

from django.apps import apps
from django.conf import settings
from django.contrib.auth import authenticate

from billing import services as billing
from billing.payments import PaymentError, take_payment
from dashboard.admin_crud import MANAGED
from dashboard.utils import create_audit_entry
from doctors_portal.models import ClinicalNote
from hospital_system.permissions import Cap, caps_for
from triage_queue.models import Encounter, QueueEntry
from triage_queue.services import FlowError, close_consultation
from wards import services as ward
from wards.models import Admission, Bed

ALL_CAPS = {value for name, value in vars(Cap).items() if name.isupper()}

# (label, required capability, method name)
MENU = [
    ("Sign off cleaned beds as vacant", Cap.BED_MANAGE, "sign_off_beds"),
    ("Admit a patient to a bed", Cap.WARD_ACTIONS, "admit"),
    ("Discharge a patient", Cap.WARD_ACTIONS, "discharge"),
    ("Record a consultation note", Cap.CLINICAL_ACTIONS, "add_note"),
    ("Complete a consultation", Cap.CLINICAL_ACTIONS, "complete_consultation"),
    ("Post provisional charges", Cap.POST_CHARGES, "post_charges"),
    ("Take a payment", Cap.TAKE_PAYMENT, "take_payment"),
    ("Records: list, create, edit", Cap.ADMIN_CONFIG, "records"),
]


def _editable_fields(model):
    return [f for f in model._meta.concrete_fields if f.editable and not f.primary_key]


class Operator:
    def __init__(self, user):
        self.user = user
        self.profile = getattr(user, "staff_profile", None)
        self.is_admin = user.is_superuser or (self.profile is not None and self.profile.role == "ADMIN")
        self.caps = ALL_CAPS if self.is_admin else caps_for(user)

    # ----- helpers -----

    def need_profile(self):
        if self.profile is None:
            raise FlowError("This account has no staff profile. Create one in the data manager first.")
        return self.profile

    def pick(self, items, label):
        items = list(items)
        if not items:
            print("  Nothing to choose from.")
            return None
        for i, item in enumerate(items, 1):
            print(f"  {i}. {label(item)}")
        raw = input("  Choose a number (blank to cancel): ").strip()
        if not raw.isdigit() or not (1 <= int(raw) <= len(items)):
            return None
        return items[int(raw) - 1]

    def audit(self, text):
        create_audit_entry(actor=self.user, action_type="DEV_OPERATOR", description=text)

    # ----- actions -----

    def sign_off_beds(self):
        staff = self.need_profile()
        beds = list(Bed.objects.filter(status="CLEANING").select_related("ward").order_by("ward__name", "code"))
        if not beds:
            print("  No beds are waiting for cleaning sign-off.")
            return
        for bed in beds:
            print(f"  {bed.ward.name} · Bed {bed.code}")
        if input("  Mark all of them vacant? [y/N] ").lower() != "y":
            return
        done = 0
        for bed in beds:
            try:
                ward.change_status(bed_id=bed.id, to_status="VACANT", staff=staff)
                done += 1
            except FlowError as exc:
                print(f"  Skipped {bed.code}: {exc}")
        self.audit(f"Signed off {done} cleaned bed(s) as vacant.")
        print(f"  Marked {done} bed(s) vacant.")

    def admit(self):
        staff = self.need_profile()
        encounters = (Encounter.objects.filter(status="OPEN")
                      .exclude(admissions__discharged_at__isnull=True)
                      .select_related("patient").order_by("-opened_at")[:50])
        encounter = self.pick(encounters, lambda e: f"{e.patient} · {e.get_encounter_type_display()}")
        if encounter is None:
            return
        bed = self.pick(Bed.objects.filter(status="VACANT").select_related("ward"),
                        lambda b: f"{b.ward.name} · Bed {b.code}")
        if bed is None:
            return
        days = int(input("  Planned stay in days [1]: ").strip() or 1)
        ward.admit(encounter=encounter, bed_id=bed.id, planned_days=days, staff=staff)
        self.audit(f"Admitted {encounter.patient} to bed {bed.code} for {days} day(s).")
        print("  Admitted.")

    def discharge(self):
        staff = self.need_profile()
        admission = self.pick(
            Admission.objects.filter(discharged_at__isnull=True).select_related("encounter__patient", "bed__ward"),
            lambda a: f"{a.encounter.patient} · {a.bed.ward.name} Bed {a.bed.code}")
        if admission is None:
            return
        _, actual = ward.discharge(admission=admission, staff=staff)
        self.audit(f"Discharged {admission.encounter.patient} after {actual} day(s).")
        print(f"  Discharged after {actual} day(s).")

    def add_note(self):
        entry = self.pick(
            QueueEntry.objects.filter(status="IN_CONSULTATION").select_related("encounter__patient", "assigned_doctor__user"),
            lambda e: f"{e.encounter.patient} · Dr. {e.assigned_doctor.user.username}")
        if entry is None:
            return
        complaint = input("  Presenting complaint: ").strip()
        diagnosis = input("  Diagnosis: ").strip()
        plan = input("  Plan (optional): ").strip()
        if not complaint or not diagnosis:
            print("  Complaint and diagnosis are required.")
            return
        ClinicalNote.objects.create(encounter=entry.encounter, author=entry.assigned_doctor,
                                    presenting_complaint=complaint, examination="",
                                    diagnosis=diagnosis, plan=plan)
        self.audit(f"Recorded a clinical note for {entry.encounter.patient}.")
        print("  Note saved.")

    def complete_consultation(self):
        entry = self.pick(
            QueueEntry.objects.filter(status__in=["IN_CONSULTATION", "ADMITTED"])
            .select_related("encounter__patient", "assigned_doctor__user"),
            lambda e: f"{e.encounter.patient} · {e.status}")
        if entry is None:
            return
        result = close_consultation(entry.encounter, entry.assigned_doctor)
        self.audit(f"Completed consultation for {entry.encounter.patient} (admitted={result['admitted']}).")
        print("  Consultation completed." if not result["admitted"] else "  Patient remains admitted.")

    def post_charges(self):
        staff = self.need_profile()
        encounter = self.pick(
            Encounter.objects.filter(charges__status="PROVISIONAL").select_related("patient").distinct(),
            lambda e: f"{e.patient} · {e.get_encounter_type_display()}")
        if encounter is None:
            return
        count = billing.post_encounter_charges(encounter, staff)
        self.audit(f"Posted {count} charge(s) for {encounter.patient}.")
        print(f"  Posted {count} charge(s).")

    def take_payment(self):
        staff = self.need_profile()
        candidates = [e for e in Encounter.objects.filter(charges__isnull=False).select_related("patient").distinct()
                      if billing.encounter_tally(e)["outstanding"] > 0]
        encounter = self.pick(candidates, lambda e: (
            f"{e.patient} · outstanding KES {billing.encounter_tally(e)['outstanding']}"))
        if encounter is None:
            return
        method = input("  Method (MANUAL or MPESA): ").strip().upper()
        outstanding = billing.encounter_tally(encounter)["outstanding"]
        amount = input(f"  Amount [{outstanding}]: ").strip() or str(outstanding)
        reference = input("  Reference or M-Pesa code: ").strip()
        try:
            payment = take_payment(encounter=encounter, method=method, amount_raw=amount,
                                   reference=reference, payer_phone="", staff=staff)
        except PaymentError as exc:
            print(f"  Not done: {exc}")
            return
        self.audit(f"Recorded {payment.get_method_display()} payment KES {payment.amount} "
                   f"({payment.receipt_number}) for {encounter.patient}.")
        print(f"  Payment recorded. Receipt {payment.receipt_number}.")

    def records(self):
        key = self.pick(sorted(MANAGED.keys()), lambda k: k)
        if key is None:
            return
        model = apps.get_model(*key.split("."))
        choice = input("  1 list, 2 create, 3 edit (delete is not available): ").strip()
        if choice == "1":
            for obj in model.objects.all()[:50]:
                print(f"  pk={obj.pk}  {obj}")
        elif choice == "2":
            self._create(model)
        elif choice == "3":
            self._edit(model)

    def _create(self, model):
        data = {}
        for f in _editable_fields(model):
            raw = input(f"  {f.name}: ").strip()
            if raw != "":
                data[f.attname] = raw
        obj = model(**data)
        obj.full_clean()
        obj.save()
        self.audit(f"Created {model._meta.label} '{obj}' via dev operator.")
        print(f"  Created: {obj}")

    def _edit(self, model):
        pk = input("  Primary key: ").strip()
        obj = model.objects.get(pk=pk)
        for f in _editable_fields(model):
            raw = input(f"  {f.name} [{getattr(obj, f.attname)}]: ").strip()
            if raw != "":
                setattr(obj, f.attname, raw)
        obj.full_clean()
        obj.save()
        self.audit(f"Edited {model._meta.label} {pk} via dev operator.")
        print(f"  Saved: {obj}")


def main():
    if not settings.DEBUG:
        sys.exit("Refusing to run: DEBUG is False. This tool is for development only.")

    print("HCMS dev operator (development only)")
    username = input("Username: ").strip()
    password = getpass("Password: ")
    user = authenticate(username=username, password=password)
    if user is None:
        sys.exit("Invalid username or password, or the account is inactive.")

    op = Operator(user)
    role = op.profile.get_role_display() if op.profile else ("Superuser" if user.is_superuser else "No profile")
    suffix = " · admin: every action except deleting" if op.is_admin else ""
    print(f"Signed in as {user.username} · {role}{suffix}")

    actions = [(label, fn) for label, cap, fn in MENU if cap in op.caps]
    if not actions:
        sys.exit("This account has no actions available.")

    while True:
        print("\nActions:")
        for i, (label, _) in enumerate(actions, 1):
            print(f"  {i}. {label}")
        print("  0. Sign out")
        raw = input("> ").strip()
        if raw in ("", "0"):
            break
        if not raw.isdigit() or not (1 <= int(raw) <= len(actions)):
            print("  Pick a number from the list.")
            continue
        label, method_name = actions[int(raw) - 1]
        try:
            getattr(op, method_name)()
        except (FlowError, PaymentError) as exc:
            print(f"  Not done: {exc}")
        except KeyboardInterrupt:
            print()
        except Exception as exc:
            print(f"  Error: {exc}")
            traceback.print_exc()


if __name__ == "__main__":
    main()
