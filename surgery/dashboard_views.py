from datetime import timedelta

from django.shortcuts import render
from django.utils import timezone

from hospital_system.permissions import Cap, requires

from . import analytics
from .models import SurgicalCase, TheatreRecord, TheatreStockBatch


@requires(Cap.SURGERY_VIEW)
def surgery_dashboard_view(request):
    today = timezone.localdate()
    now = timezone.now()
    summary = analytics.summary(today)
    series = analytics.chart_series(today, 30)
    chart_data = {
        "daily": series["daily"],
        "severity": series["severity"],
        "top": analytics.top_procedures(today.replace(day=1)),
    }
    upcoming = (SurgicalCase.objects.filter(status="BOOKED", scheduled_for__gte=now)
                .select_related("patient", "procedure").order_by("scheduled_for")[:10])
    in_theatre = (TheatreRecord.objects.filter(sign_out_at__isnull=True, case__status="BOOKED")
                  .select_related("case__patient", "case__procedure"))
    expiring = (TheatreStockBatch.objects.select_related("item")
                .filter(quantity_on_hand__gt=0, expiry_date__lte=today + timedelta(days=analytics.EXPIRY_WARNING_DAYS))
                .order_by("expiry_date")[:10])
    return render(request, "surgery/dashboard.html", {
        "summary": summary, "chart_data": chart_data, "upcoming": upcoming,
        "in_theatre": in_theatre, "expiring": expiring,
    })
