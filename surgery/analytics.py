"""
Surgery figures for the dashboards and the overview charts. Every number is read from the database.
Counts are by the day a case was booked, so a day's bar shows the cases that day's list started with.
"""
from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import SurgicalCase, SurgicalComplication, TheatreRecord, TheatreStockBatch

STATUS_SERIES = [("COMPLETED", "Completed"), ("BOOKED", "Still open"), ("CANCELLED", "Cancelled")]
EXPIRY_WARNING_DAYS = 60


def _days(start, end):
    day, out = start, []
    while day <= end:
        out.append(day)
        day += timedelta(days=1)
    return out


def cases_daily(start, end):
    """Cases booked per day, split by status. One value per day, so the chart lines up."""
    days = _days(start, end)
    rows = (SurgicalCase.objects.filter(booked_at__date__gte=start, booked_at__date__lte=end)
            .annotate(day=TruncDate("booked_at")).values("day", "status").annotate(n=Count("id")))
    counts = {(r["day"], r["status"]): r["n"] for r in rows}
    return {
        "labels": [d.strftime("%d %b") for d in days],
        "series": [{"label": label, "data": [counts.get((d, key), 0) for d in days]}
                   for key, label in STATUS_SERIES],
    }


def complications_by_severity(since):
    rows = (SurgicalComplication.objects.filter(recorded_at__gte=since)
            .values("severity").annotate(n=Count("id")))
    got = {r["severity"]: r["n"] for r in rows}
    return [{"label": label, "n": got.get(key, 0)} for key, label in SurgicalComplication.SEVERITY_CHOICES]


def top_procedures(month_start, limit=5):
    rows = (SurgicalCase.objects.filter(booked_at__date__gte=month_start)
            .values("procedure__name").annotate(n=Count("id")).order_by("-n", "procedure__name")[:limit])
    return [{"label": r["procedure__name"], "n": r["n"]} for r in rows]


def summary(today):
    now = timezone.now()
    month_start = today.replace(day=1)
    cases = SurgicalCase.objects.all()
    return {
        "cases_month": cases.filter(booked_at__date__gte=month_start).count(),
        "completed_month": cases.filter(status="COMPLETED", booked_at__date__gte=month_start).count(),
        "in_theatre": TheatreRecord.objects.filter(sign_out_at__isnull=True, case__status="BOOKED").count(),
        "upcoming": cases.filter(status="BOOKED", scheduled_for__gte=now).count(),
        "complications_30d": SurgicalComplication.objects.filter(
            recorded_at__date__gte=today - timedelta(days=29)).count(),
        "stock_expiring": TheatreStockBatch.objects.filter(
            quantity_on_hand__gt=0, expiry_date__gte=today,
            expiry_date__lte=today + timedelta(days=EXPIRY_WARNING_DAYS)).count(),
    }


def chart_series(today, window_days):
    start = today - timedelta(days=window_days - 1)
    return {
        "daily": cases_daily(start, today),
        "severity": complications_by_severity(timezone.now() - timedelta(days=window_days)),
    }
