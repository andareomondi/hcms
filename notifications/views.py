from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import services


@login_required
def dropdown_view(request):
    profile = request.staff_profile
    recent = list(services.unread_for(profile).order_by("-created_at")[:10]) if profile else []
    return render(request, "notifications/_dropdown.html", {"recent_notifications": recent})


@login_required
@require_POST
def mark_read_view(request, notification_id):
    if request.staff_profile:
        services.mark_read(request.staff_profile, notification_id)
    link = request.POST.get("next") or "/"
    return redirect(link)


@login_required
@require_POST
def mark_all_read_view(request):
    if request.staff_profile:
        services.mark_all_read(request.staff_profile)
    return redirect(request.META.get("HTTP_REFERER", "/"))
