"""
Chart series for the dashboard overview. Built from the same live data as the cards.
"""
from collections import Counter
from datetime import date, timedelta

from django.db.models import Count

from core_patients.models import StaffProfile
from duty.models import DutySession, Room
from wards.models import Bed

from .export import finance_daily, metrics_daily

WINDOW_DAYS = 14


def _bucket(priority):
    # Matches the labels used on the waiting list: 3 resuscitation, 2 urgent, anything else routine.
    if priority == 3:
        return "Resuscitation"
    if priority == 2:
        return "Urgent"
    return "Routine"


def _room_status():
    occupied = (DutySession.objects.filter(clocked_out_at__isnull=True, room__isnull=False)
                .values("room_id").distinct().count())
    active = Room.objects.filter(is_active=True).count()
    closed = Room.objects.filter(is_active=False).count()
    return [
        {"label": "Occupied", "n": occupied},
        {"label": "Free", "n": max(active - occupied, 0)},
        {"label": "Closed", "n": closed},
    ]


def _duty_by_role():
    on_duty_ids = DutySession.objects.filter(clocked_out_at__isnull=True).values("staff_id")
    return list(StaffProfile.objects.filter(id__in=on_duty_ids)
                .values("role").annotate(n=Count("id")).order_by("role"))


def build_overview_charts(caps, today, waiting_list):
    start = today - timedelta(days=WINDOW_DAYS - 1)
    ops = metrics_daily(start, today)

    waiting = Counter(_bucket(getattr(e, "priority", None)) for e in waiting_list)

    data = {
        "labels": [date.fromisoformat(r["date"]).strftime("%d %b") for r in ops],
        "patients": [r["patients_registered"] for r in ops],
        "visits": [r["visits_opened"] for r in ops],
        "admissions": [r["admissions"] for r in ops],
        "bed_status": list(Bed.objects.values("status").annotate(n=Count("id")).order_by("status")),
        "priority": [{"label": k, "n": waiting.get(k, 0)}
                     for k in ("Resuscitation", "Urgent", "Routine")],
        "room_status": _room_status(),
        "duty_roles": _duty_by_role(),
        "show_finance": "VIEW_FINANCE" in caps,
    }

    if data["show_finance"]:
        fin = finance_daily(start, today)
        data["charges"] = [float(r["charges_posted_kes"]) for r in fin]
        data["payments_mpesa"] = [float(r["payments_mpesa_kes"]) for r in fin]
        data["payments_manual"] = [float(r["payments_manual_kes"]) for r in fin]

    return data
