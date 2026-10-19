import os
import random
from datetime import date, timedelta

import django
from django.utils import timezone

# Setup Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hospital_system.settings")
django.setup()

from django.contrib.auth.models import User

from billing.models import BillingAccount, Charge
from core_patients.models import (
    Allergen,
    AllergenClass,
    Department,
    Patient,
    PatientAllergy,
    StaffProfile,
)
from doctors_portal.models import ClinicalNote
from pharmacy.models import Medication, MedicationOrder, StockItem
from radiology.models import ScanOrder
from triage_queue.models import Encounter, QueueEntry, TriageVitals
from wards.models import Admission, Bed, Ward


def run_seed():
    print("🌱 Starting HCMS Bulk Database Seeding (Safe, Unique & Truncation-Free)...")

    # 1. Departments (150+ unique records)
    print("Creating departments...")
    dept_kinds = ["CLINICAL", "WARD", "DIAGNOSTIC", "PHARMACY", "ADMIN", "BILLING"]
    departments = []
    for i in range(1, 155):
        d, _ = Department.objects.get_or_create(
            code=f"DEPT-{i:03d}",
            defaults={
                "name": f"Specialty & Support Department {i}", 
                "kind": random.choice(dept_kinds)
            }
        )
        departments.append(d)

    # 2. Staff Profiles (150+ unique records)
    print("Creating staff profiles...")
    roles = ["DOCTOR", "TRIAGE_NURSE", "NURSE", "PHARMACIST", "RADIOLOGIST", "BILLING", "ADMIN"]
    staff_profiles = []
    for i in range(1, 155):
        u, _ = User.objects.get_or_create(
            username=f"staff_member_{i}",
            defaults={
                "email": f"staff{i}@hcms.local", 
                "first_name": f"Staff{i}", 
                "last_name": f"Member{i}"
            }
        )
        sp, _ = StaffProfile.objects.get_or_create(
            user=u,
            defaults={
                "employee_id": f"EMP-BULK-{i:04d}",
                "role": random.choice(roles),
                "department": random.choice(departments),
                "is_active": True
            }
        )
        staff_profiles.append(sp)

    admin_profile = staff_profiles[0]

    # 3. Allergens & Classes (Unique names per iteration)
    print("Creating allergen database...")
    allergen_classes = []
    for i in range(1, 15):
        ac, _ = AllergenClass.objects.get_or_create(
            code=f"CLASS-{i:03d}", 
            defaults={"name": f"Allergen Class Group {i}"}
        )
        allergen_classes.append(ac)

    allergens = []
    for i in range(1, 155):
        alg, _ = Allergen.objects.get_or_create(
            code=f"ALG-BULK-{i:03d}",
            defaults={
                "display_name": f"Allergen Compound {i}", 
                "allergen_class": random.choice(allergen_classes)
            }
        )
        allergens.append(alg)

    # 4. Patients (150+ unique records)
    print("Registering patients...")
    first_names = ["Alice", "John", "Grace", "David", "Mary", "James", "Esther", "Peter", "Fatuma", "Brian", "Amina", "Kevin", "Sarah"]
    last_names = ["Wanjiku", "Omondi", "Kipkorir", "Otieno", "Mora", "Kamau", "Abdi", "Nekesa", "Mutua", "Juma", "Cheruiyot", "Njeri"]

    patients = []
    for i in range(1, 160):
        patient, _ = Patient.objects.get_or_create(
            id_number=f"ID-BULK-2026-{i:04d}",
            defaults={
                "first_name": random.choice(first_names),
                "last_name": f"{random.choice(last_names)}-{i}",
                "date_of_birth": date(random.randint(1950, 2012), random.randint(1, 12), random.randint(1, 28)),
                "sex": random.choice(["M", "F"]),
                "id_type": "NATIONAL_ID",
                "phone": f"+2547{random.randint(10000000, 99999999)}",
                "created_by": admin_profile,
            }
        )
        if i % 2 == 0:
            PatientAllergy.objects.get_or_create(
                patient=patient,
                allergen=random.choice(allergens),
                defaults={"severity": random.choice(["MILD", "MODERATE", "SEVERE"]), "status": "ACTIVE", "recorded_by": admin_profile}
            )
        patients.append(patient)

    # 5. Medications & Stock (150+ unique records)
    print("Adding medications and stock items...")
    med_forms = ["Tablet", "Capsule", "Syrup", "Injection", "Inhaler"]
    strengths = ["10mg", "50mg", "100mg", "250mg", "500mg", "1g"]
    medications = []
    for i in range(1, 160):
        med, _ = Medication.objects.get_or_create(
            brand_name=f"BrandMedBulk-{i}",
            defaults={
                "generic_name": f"GenericDrug-{i}",
                "form": random.choice(med_forms),
                "strength": random.choice(strengths),
                "unit_price": round(random.uniform(10.0, 1000.0), 2),
                "is_active": True
            }
        )
        StockItem.objects.get_or_create(
            medication=med,
            defaults={"quantity": random.randint(50, 5000), "reorder_level": 50}
        )
        medications.append(med)

    # 6. Wards & Beds (Short codes safe under 20 chars max_length)
    print("Configuring wards and beds...")
    ward_dept_obj = [d for d in departments if d.kind == "WARD"][0]
    wards = []
    for i in range(1, 12):
        w, _ = Ward.objects.get_or_create(
            name=f"Inpatient Medical Ward {i}",
            defaults={"department": ward_dept_obj, "bed_day_rate": random.randint(2000, 9000)}
        )
        wards.append(w)

    beds = []
    for idx, w in enumerate(wards, start=1):
        for b_num in range(1, 16):
            safe_code = f"W{idx}-B{b_num:02d}"  # Safe short format e.g., W1-B01
            b, _ = Bed.objects.get_or_create(
                ward=w,
                code=safe_code,
                defaults={"status": random.choice(["VACANT", "OCCUPIED", "CLEANING"])}
            )
            beds.append(b)

    # 7. Encounters, Vitals, Queue, Notes, Orders, Scans
    print("Opening clinical encounters, vitals, queue entries, and orders...")
    complaints = ["Fever and chills", "Acute abdominal pain", "Headache and fatigue", "Shortness of breath", "Trauma / laceration"]
    diagnoses = ["Acute Infection", "Gastroenteritis", "Essential Hypertension", "Asthma Exacerbation", "Soft Tissue Injury"]

    for i in range(1, 165):
        patient = patients[i % len(patients)]
        doc = random.choice([sp for sp in staff_profiles if sp.role == "DOCTOR"] or staff_profiles)
        nurse = random.choice([sp for sp in staff_profiles if sp.role in ("TRIAGE_NURSE", "NURSE")] or staff_profiles)

        encounter, _ = Encounter.objects.get_or_create(
            patient=patient,
            status=random.choice(["OPEN", "CLOSED"]),
            defaults={
                "encounter_type": random.choice(["OUTPATIENT", "EMERGENCY", "OBSERVATION"]),
                "payer_type": random.choice(["SELF_PAY", "INSURANCE", "CORPORATE"]),
                "opened_by": nurse,
                "opened_at": timezone.now() - timedelta(days=random.randint(0, 30))
            }
        )

        TriageVitals.objects.get_or_create(
            encounter=encounter,
            defaults={
                "recorded_by": nurse,
                "temperature_c": round(random.uniform(36.0, 39.5), 1),
                "pulse_bpm": random.randint(60, 130),
                "systolic_mmhg": random.randint(100, 160),
                "diastolic_mmhg": random.randint(60, 100),
                "spo2_percent": random.randint(92, 100),
                "weight_kg": round(random.uniform(45.0, 110.0), 1),
                "notes": f"Bulk vital check #{i}"
            }
        )

        QueueEntry.objects.get_or_create(
            encounter=encounter,
            defaults={
                "assigned_doctor": doc,
                "priority": random.randint(1, 3),
                "status": "WAITING" if encounter.status == "OPEN" else "DISCHARGED"
            }
        )

        ClinicalNote.objects.get_or_create(
            encounter=encounter,
            defaults={
                "author": doc,
                "presenting_complaint": random.choice(complaints),
                "examination": "General examination normal.",
                "diagnosis": random.choice(diagnoses),
                "plan": "Prescribe medication and monitor."
            }
        )

        MedicationOrder.objects.get_or_create(
            encounter=encounter,
            medication=random.choice(medications),
            defaults={
                "ordered_by": doc,
                "route": random.choice(["ORAL", "IV", "IM"]),
                "fulfilment": random.choice(["NURSE_ADMINISTER", "DISPENSE"]),
                "dose_text": "1 tablet",
                "frequency_text": "BD",
                "duration_days": 5,
                "instructions": "Take after food"
            }
        )

        ScanOrder.objects.get_or_create(
            encounter=encounter,
            defaults={
                "ordered_by": doc,
                "modality": random.choice(["X-Ray", "CT Scan", "MRI", "Ultrasound"]),
                "body_part": random.choice(["Chest", "Abdomen", "Brain", "Left Knee"]),
                "clinical_indication": "Rule out acute pathology.",
                "status": random.choice(["ORDERED", "REPORTED"])
            }
        )

    # 8. Admissions
    print("Creating ward admissions...")
    for i in range(1, 160):
        bed = beds[i % len(beds)]
        patient = patients[i % len(patients)]
        enc, _ = Encounter.objects.get_or_create(
            patient=patient,
            status="OPEN",
            defaults={"encounter_type": "OBSERVATION", "payer_type": "INSURANCE", "opened_by": admin_profile}
        )
        if bed.status == "VACANT":
            Admission.objects.get_or_create(
                encounter=enc,
                bed=bed,
                defaults={"admitted_at": timezone.now() - timedelta(days=1), "admitted_by": admin_profile}
            )
            bed.status = "OCCUPIED"
            bed.save(update_fields=["status"])

    # 9. Billing Accounts & Charges
    print("Creating billing accounts and charges...")
    for i, p in enumerate(patients[:160]):
        acc, _ = BillingAccount.objects.get_or_create(owner=p, defaults={"is_active": True})
        enc = p.encounters.first()
        if enc:
            Charge.objects.get_or_create(
                encounter=enc,
                source_type=random.choice(["CONSULTATION", "PHARMACY", "LAB", "RADIOLOGY"]),
                defaults={
                    "treated_patient": p,
                    "billing_account": acc,
                    "description": f"Bulk service charge #{i}",
                    "quantity": 1,
                    "unit_price": round(random.uniform(200.0, 5000.0), 2),
                    "amount": round(random.uniform(200.0, 5000.0), 2),
                    "status": random.choice(["PROVISIONAL", "POSTED"]),
                    "created_by": admin_profile,
                }
            )

    print("✨ Database successfully bulk-seeded! Existing records were safely skipped.")

if __name__ == "__main__":
    run_seed()
