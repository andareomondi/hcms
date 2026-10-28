"""
HCMS production seed: hospital structure and reference catalogues only.

Creates or updates:
  - departments
  - allergen classes and allergens
  - wards and beds
  - the laboratory test catalogue (names, specimens, reference ranges, prices)

Does NOT create: patients, staff accounts, encounters, orders, charges, payments
or any clinical record. Create the first administrator with `python manage.py createsuperuser`.

Safe to run repeatedly. Every record is matched on its code or name and updated in place,
so names, prices and ranges can be changed here and re-applied without duplicates.
Existing bed statuses are never changed.

Reference ranges and prices below are PLACEHOLDERS. They must be reviewed by the
laboratory lead and the hospital before go-live.

Usage (project root, virtualenv active):
    python seed_db.py
"""
import os
from decimal import Decimal

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hospital_system.settings")
django.setup()

from django.db import transaction

from core_patients.models import Allergen, AllergenClass, Department
from lab.models import LabTest
from wards.models import Bed, Ward

# ---------------------------------------------------------------------------
# Departments. `kind` must be a value your Department.kind choices accept.
# ---------------------------------------------------------------------------
DEPARTMENTS = [
    # code,       name,                           kind
    ("GEN-MED",   "General Medicine",             "CLINICAL"),
    ("GEN-SURG",  "General Surgery",              "CLINICAL"),
    ("PAED",      "Paediatrics",                  "CLINICAL"),
    ("OBGYN",     "Obstetrics & Gynaecology",     "CLINICAL"),
    ("EMERG",     "Emergency",                    "CLINICAL"),
    ("WARD-GEN",  "General Inpatient Wards",      "WARD"),
    ("RAD",       "Radiology",                    "DIAGNOSTIC"),
    ("LAB",       "Laboratory",                   "DIAGNOSTIC"),
    ("PHARM",     "Pharmacy",                     "PHARMACY"),
    ("BILL",      "Billing",                      "BILLING"),
    ("ADMIN",     "Administration",               "ADMIN"),
]


# ---------------------------------------------------------------------------
# Allergen classes and allergens (matched by code).
# ---------------------------------------------------------------------------
ALLERGEN_CLASSES = [
    # code,         name
    ("PENICILLIN",  "Penicillins"),
    ("CEPHALOSPORIN", "Cephalosporins"),
    ("SULFONAMIDE", "Sulfonamides"),
    ("NSAID",       "Non-steroidal anti-inflammatories"),
    ("OPIOID",      "Opioids"),
    ("LATEX",       "Latex"),
    ("FOOD",        "Foods"),
]

ALLERGENS = [
    # code,           display name,                 class code
    ("PEN-BENZYL",    "Benzylpenicillin",           "PENICILLIN"),
    ("AMOX",          "Amoxicillin",                "PENICILLIN"),
    ("CEF-CEFTRI",    "Ceftriaxone",                "CEPHALOSPORIN"),
    ("SULF-COTRI",    "Co-trimoxazole",             "SULFONAMIDE"),
    ("NSAID-IBU",     "Ibuprofen",                  "NSAID"),
    ("NSAID-DIC",     "Diclofenac",                 "NSAID"),
    ("OPI-MORPH",     "Morphine",                   "OPIOID"),
    ("LATEX-GEN",     "Natural rubber latex",       "LATEX"),
    ("FOOD-PEANUT",   "Peanuts",                    "FOOD"),
]


# ---------------------------------------------------------------------------
# Wards and beds. Bed codes are generated as <prefix>-B01 .. <prefix>-B<n>.
# bed_day_rate is a PLACEHOLDER tariff in KES.
# ---------------------------------------------------------------------------
WARDS = [
    # ward name,                  dept code,  bed prefix, beds, bed-day rate (KES)
    ("General Medical Ward",      "WARD-GEN", "GMW",      12,   "2500.00"),
    ("General Surgical Ward",     "WARD-GEN", "GSW",      12,   "2500.00"),
    ("Paediatric Ward",           "PAED",     "PAW",      10,   "2000.00"),
    ("Maternity Ward",            "OBGYN",    "MAT",      10,   "2200.00"),
]


# ---------------------------------------------------------------------------
# Laboratory test catalogue (matched by code).
#   result_type: NUMERIC, POSITIVE_NEGATIVE or TEXT
#   Values for ranges and critical thresholds are adult defaults only; sex- and
#   age-specific ranges are not modelled yet (see the lab pack, known limits).
# ---------------------------------------------------------------------------
LAB_TESTS = [
    # Haematology (blood counts)
    dict(code="HB",     name="Haemoglobin",            section="HAEMATOLOGY", result_type="NUMERIC",
         unit="g/dL", specimen="EDTA blood", reference_low="12.0", reference_high="17.0",
         critical_low="7.0", critical_high="20.0", price="300.00"),
    dict(code="WBC",    name="White blood cell count", section="HAEMATOLOGY", result_type="NUMERIC",
         unit="x10^9/L", specimen="EDTA blood", reference_low="4.0", reference_high="11.0",
         critical_low="2.0", critical_high="30.0", price="400.00"),
    dict(code="PLT",    name="Platelet count",         section="HAEMATOLOGY", result_type="NUMERIC",
         unit="x10^9/L", specimen="EDTA blood", reference_low="150", reference_high="400",
         critical_low="50", critical_high="1000", price="400.00"),
    dict(code="FBC",    name="Full blood count (CBC)", section="HAEMATOLOGY", result_type="TEXT",
         unit="", specimen="EDTA blood", reference_text="Within reference limits",
         price="800.00"),
    dict(code="MP",     name="Malaria parasite (smear or RDT)", section="PARASITOLOGY",
         result_type="POSITIVE_NEGATIVE", unit="", specimen="Blood (thick and thin film, or RDT)",
         reference_text="Negative", price="350.00"),

    # Chemistry (blood and body fluids)
    dict(code="GLU-F",  name="Blood glucose (fasting)", section="CHEMISTRY", result_type="NUMERIC",
         unit="mmol/L", specimen="Fluoride grey-top", reference_low="3.9", reference_high="5.5",
         critical_low="2.5", critical_high="25.0", price="250.00"),
    dict(code="CREAT",  name="Creatinine",             section="CHEMISTRY", result_type="NUMERIC",
         unit="umol/L", specimen="Serum", reference_low="62", reference_high="106",
         critical_high="500", price="450.00"),
    dict(code="UREA",   name="Urea",                   section="CHEMISTRY", result_type="NUMERIC",
         unit="mmol/L", specimen="Serum", reference_low="2.5", reference_high="7.8",
         critical_high="30", price="450.00"),
    dict(code="ALT",    name="Alanine aminotransferase (ALT)", section="CHEMISTRY",
         result_type="NUMERIC", unit="U/L", specimen="Serum", reference_low="7", reference_high="56",
         price="500.00"),
    dict(code="NA",     name="Sodium",                 section="CHEMISTRY", result_type="NUMERIC",
         unit="mmol/L", specimen="Serum", reference_low="135", reference_high="145",
         critical_low="120", critical_high="160", price="450.00"),
    dict(code="K",      name="Potassium",              section="CHEMISTRY", result_type="NUMERIC",
         unit="mmol/L", specimen="Serum", reference_low="3.5", reference_high="5.1",
         critical_low="2.5", critical_high="6.5", price="450.00"),

    # Urinalysis
    dict(code="UDIP",   name="Urine dipstick",         section="URINALYSIS", result_type="TEXT",
         unit="", specimen="Urine", reference_text="Negative", price="200.00"),
    dict(code="UPREG",  name="Urine pregnancy test",   section="URINALYSIS", result_type="POSITIVE_NEGATIVE",
         unit="", specimen="Urine", reference_text="Negative", price="250.00"),
]


def _dec(value):
    return None if value in (None, "") else Decimal(str(value))


def seed_departments():
    count = 0
    for code, name, kind in DEPARTMENTS:
        Department.objects.update_or_create(code=code, defaults={"name": name, "kind": kind})
        count += 1
    return count


def seed_allergens():
    classes = {}
    for code, name in ALLERGEN_CLASSES:
        obj, _ = AllergenClass.objects.update_or_create(code=code, defaults={"name": name})
        classes[code] = obj
    for code, display, class_code in ALLERGENS:
        Allergen.objects.update_or_create(
            code=code,
            defaults={"display_name": display, "allergen_class": classes[class_code]},
        )
    return len(classes), len(ALLERGENS)


def seed_wards_and_beds():
    departments = {d.code: d for d in Department.objects.all()}
    beds_created = 0
    for name, dept_code, prefix, bed_count, rate in WARDS:
        ward, _ = Ward.objects.update_or_create(
            name=name,
            defaults={"department": departments[dept_code], "bed_day_rate": Decimal(rate)},
        )
        for n in range(1, bed_count + 1):
            # get_or_create with a default status: an existing bed keeps its current status.
            _, created = Bed.objects.get_or_create(
                ward=ward, code=f"{prefix}-B{n:02d}", defaults={"status": "VACANT"},
            )
            beds_created += int(created)
    return len(WARDS), beds_created


def seed_lab_tests():
    for row in LAB_TESTS:
        LabTest.objects.update_or_create(
            code=row["code"],
            defaults={
                "name": row["name"],
                "section": row["section"],
                "result_type": row["result_type"],
                "unit": row.get("unit", ""),
                "specimen": row["specimen"],
                "reference_low": _dec(row.get("reference_low")),
                "reference_high": _dec(row.get("reference_high")),
                "reference_text": row.get("reference_text", ""),
                "critical_low": _dec(row.get("critical_low")),
                "critical_high": _dec(row.get("critical_high")),
                "price": Decimal(row["price"]),
                "is_active": True,
            },
        )
    return len(LAB_TESTS)


def main():
    with transaction.atomic():
        departments = seed_departments()
        classes, allergens = seed_allergens()
        wards, beds = seed_wards_and_beds()
        tests = seed_lab_tests()

    print("HCMS structure seed complete.")
    print(f"  Departments:        {departments}")
    print(f"  Allergen classes:   {classes}")
    print(f"  Allergens:          {allergens}")
    print(f"  Wards:              {wards}  (new beds created: {beds})")
    print(f"  Laboratory tests:   {tests}")
    print("Reminder: ranges and prices are placeholders pending hospital review.")


if __name__ == "__main__":
    main()
