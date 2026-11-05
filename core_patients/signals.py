import secrets
import string

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Department, StaffProfile

_ALPHABET = string.ascii_lowercase + string.digits


def _random_employee_id():
    """Returns an ID like 'admin-k3x9q2' that no staff profile already uses."""
    while True:
        candidate = "admin-" + "".join(secrets.choice(_ALPHABET) for _ in range(6))
        if not StaffProfile.objects.filter(employee_id=candidate).exists():
            return candidate


def ensure_admin_profile(user):
    """
    Gives a user an administrator staff profile, if they do not already have one.
    Safe to call more than once. Returns the new profile, or None if one already existed.
    """
    if StaffProfile.objects.filter(user=user).exists():
        return None
    department, _ = Department.objects.get_or_create(
        code="ADMIN",
        defaults={"name": "Administration", "kind": "ADMIN"},
    )
    return StaffProfile.objects.create(
        user=user,
        employee_id=_random_employee_id(),
        role="ADMIN",
        department=department,
        is_active=True,
    )


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_superuser_profile(sender, instance, created, raw=False, **kwargs):
    """
    Runs when a user is saved. Only a newly created superuser gets a staff profile.
    Ordinary staff accounts are created through the approval flow, which makes their own profile.
    """
    if raw:  # loaddata: fixtures carry their own profiles
        return
    if created and instance.is_superuser:
        ensure_admin_profile(instance)
