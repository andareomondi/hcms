from .services import unread_for


def notifications(request):
    """Makes the bell badge and dropdown data available on every page, scoped to the logged-in profile."""
    if not request.user.is_authenticated:
        return {}
    profile = getattr(request, "staff_profile", None)
    if profile is None:
        return {"unread_notification_count": 0, "recent_notifications": []}
    qs = unread_for(profile)
    return {
        "unread_notification_count": qs.count(),
        "recent_notifications": list(qs.order_by("-created_at")[:8]),
    }
