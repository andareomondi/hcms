from django.utils import timezone

from duty.services import on_duty_staff

from .models import Notification


def notify(recipients, kind, message, link=""):
    """Create one notification per recipient profile."""
    rows = [Notification(recipient=r, kind=kind, message=message, link=link) for r in recipients if r]
    if rows:
        Notification.objects.bulk_create(rows)
    return len(rows)


def notify_on_duty(role, kind, message, link=""):
    """Broadcast to every staff member of a role who is currently clocked in."""
    return notify(list(on_duty_staff(role=role)), kind, message, link)


def unread_for(profile):
    if profile is None:
        return Notification.objects.none()
    return Notification.objects.filter(recipient=profile, read_at__isnull=True)


def mark_read(profile, notification_id):
    return Notification.objects.filter(pk=notification_id, recipient=profile, read_at__isnull=True) \
        .update(read_at=timezone.now())


def mark_all_read(profile):
    return unread_for(profile).update(read_at=timezone.now())
