from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import services


def _safe_next(request, default="/"):
    """Only allow redirects to this site (relative paths or our own host)."""
    target = request.POST.get("next") or default
    if url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return default


@login_required
def dropdown_view(request):
    profile = request.staff_profile
    recent = list(services.unread_for(profile).order_by("-created_at")[:10]) if profile else []
    return render(request, "notifications/_dropdown.html", {"recent_notifications": recent})


@login_required
def count_view(request):
    profile = request.staff_profile
    count = services.unread_for(profile).count() if profile else 0
    return render(request, "notifications/_badge.html", {"count": count})


@login_required
@require_POST
def mark_read_view(request, notification_id):
    if request.staff_profile:
        services.mark_read(request.staff_profile, notification_id)
    return redirect(_safe_next(request))


@login_required
@require_POST
def mark_all_read_view(request):
    if request.staff_profile:
        services.mark_all_read(request.staff_profile)
    return redirect(_safe_next(request))
