"""
Full data export: every model to CSV inside an AES-256 zip, plus daily finance and operations metrics.
Used by the admin web export (dashboard/export_views.py) and the export_all_data management command.
"""
import csv
import hashlib
import io
import json
from datetime import timedelta

import pyzipper
from django.apps import apps
from django.conf import settings
from django.db import models
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

# Never exported, in any model (including historical copies).
EXCLUDED_FIELDS = {"password", "key", "session_data", "device_hash", "token_hash",
                   "pending_hash", "secret", "otp_secret"}
# Whole models never exported.
EXCLUDED_MODELS = {"sessions.Session"}


class ExportError(Exception):
    """Shown to the admin as-is."""


def included_models():
    for model in apps.get_models():
        if model._meta.label in EXCLUDED_MODELS:
            continue
        yield model


def exportable_fields(model):
    return [f for f in model._meta.concrete_fields if f.name not in EXCLUDED_FIELDS]


def row_counts():
    return {m._meta.label: m._default_manager.count() for m in included_models()}


def _cell(field, obj):
    value = getattr(obj, field.attname)
    if value is None:
        return ""
    if isinstance(field, models.FileField):
        return value  # the stored path only; file bytes are added when include_files is set
    return value


def build_export(path, *, passphrase, include_files=False):
    """
    Streams every included model to a CSV inside an AES-256 zip. Returns the manifest dict.
    Rows are written as they are read, so memory use stays flat.
    """
    if len(passphrase or "") < settings.EXPORT_MIN_PASSPHRASE:
        raise ExportError(f"The export passphrase must be at least {settings.EXPORT_MIN_PASSPHRASE} characters.")

    manifest = {
        "exported_at": timezone.now().isoformat(),
        "tables": {},
        "files": [],
        "files_included": include_files,
    }
    with pyzipper.AESZipFile(path, "w", compression=pyzipper.ZIP_DEFLATED,
                             encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(passphrase.encode("utf-8"))
        for model in included_models():
            fields = exportable_fields(model)
            name = f"data/{model._meta.label}.csv"
            count = 0
            with zf.open(name, "w") as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                writer = csv.writer(text)
                writer.writerow([f.name for f in fields])
                qs = model._default_manager.all().order_by("pk")
                for obj in qs.iterator(chunk_size=2000):
                    writer.writerow([_cell(f, obj) for f in fields])
                    count += 1
                text.flush()
                text.detach()
            manifest["tables"][model._meta.label] = count

            if include_files:
                _add_files(zf, model, manifest)

        _add_metrics(zf, manifest)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
    return manifest


def _add_files(zf, model, manifest):
    file_fields = [f for f in model._meta.concrete_fields if isinstance(f, models.FileField)]
    for f in file_fields:
        for obj in model._default_manager.exclude(**{f.name: ""}).iterator(chunk_size=500):
            fh = getattr(obj, f.name)
            if not fh:
                continue
            try:
                fh.open("rb")
                data = fh.read()
                fh.close()
            except (FileNotFoundError, OSError):
                manifest["files"].append({"path": fh.name, "status": "MISSING"})
                continue
            zf.writestr(f"files/{fh.name}", data)
            manifest["files"].append({"path": fh.name, "sha256": hashlib.sha256(data).hexdigest(),
                                      "status": "INCLUDED"})


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Charts and metrics
# ---------------------------------------------------------------------------

def _days(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def finance_daily(start, end):
    """One row per day: charges created, charges posted, payments, and payments by method."""
    from billing.models import Charge, Payment

    created = {r["d"]: r["t"] for r in (Charge.objects.exclude(status="VOIDED")
               .filter(created_at__date__range=(start, end))
               .annotate(d=TruncDate("created_at")).values("d").annotate(t=Sum("amount")))}
    posted = {r["d"]: r["t"] for r in (Charge.objects.filter(posted_at__date__range=(start, end))
              .annotate(d=TruncDate("posted_at")).values("d").annotate(t=Sum("amount")))}
    paid = {}
    by_method = {}
    for r in (Payment.objects.filter(recorded_at__date__range=(start, end))
              .annotate(d=TruncDate("recorded_at")).values("d", "method")
              .annotate(t=Sum("amount"))):
        paid[r["d"]] = paid.get(r["d"], 0) + r["t"]
        by_method.setdefault(r["method"], {})[r["d"]] = r["t"]

    rows = []
    for d in _days(start, end):
        rows.append({
            "date": d.isoformat(),
            "charges_created_kes": str(created.get(d, 0) or 0),
            "charges_posted_kes": str(posted.get(d, 0) or 0),
            "payments_kes": str(paid.get(d, 0) or 0),
            "payments_manual_kes": str(by_method.get("MANUAL", {}).get(d, 0) or 0),
            "payments_mpesa_kes": str(by_method.get("MPESA", {}).get(d, 0) or 0),
        })
    return rows


def metrics_daily(start, end):
    """One row per day: patients, visits, medication and scan orders, admissions."""
    from core_patients.models import Patient
    from pharmacy.models import MedicationOrder
    from radiology.models import ScanOrder
    from triage_queue.models import Encounter
    from wards.models import Admission

    def per_day(qs, field):
        return {r["d"]: r["n"] for r in qs.filter(**{f"{field}__date__range": (start, end)})
                .annotate(d=TruncDate(field)).values("d").annotate(n=Count("id"))}

    patients = per_day(Patient.objects.all(), "created_at")
    visits = per_day(Encounter.objects.all(), "opened_at")
    meds = per_day(MedicationOrder.objects.all(), "ordered_at")
    scans = per_day(ScanOrder.objects.all(), "ordered_at")
    admissions = per_day(Admission.objects.all(), "admitted_at")
    return [{"date": d.isoformat(), "patients_registered": patients.get(d, 0),
             "visits_opened": visits.get(d, 0), "medication_orders": meds.get(d, 0),
             "scan_orders": scans.get(d, 0), "admissions": admissions.get(d, 0)}
            for d in _days(start, end)]


def _add_metrics(zf, manifest):
    today = timezone.localdate()
    start = today - timedelta(days=365 * 5)   # five years back; widen if needed
    for name, rows in (("metrics/finance_daily.csv", finance_daily(start, today)),
                       ("metrics/operations_daily.csv", metrics_daily(start, today))):
        buf = io.StringIO()
        if rows:
            w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        zf.writestr(name, buf.getvalue())
        manifest["tables"][name] = len(rows)
