from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import AuditLog

User = get_user_model()

# Apps to monitor for database changes
MONITORED_APPS = ["core_patients", "pharmacy", "wards", "billing", "triage_queue", "doctors_portal"]

@receiver(post_save)
def auto_log_model_save(sender, instance, created, **kwargs):
    """Automatically log CREATE and UPDATE events across all monitored models."""
    app_label = sender._meta.app_label
    if app_label in MONITORED_APPS and sender != AuditLog:
        action_type = "CREATE" if created else "UPDATE"
        model_name = sender._meta.verbose_name.title()
        
        # Build readable description based on object representation
        desc = f"{'Created' if created else 'Updated'} {model_name}: {instance}"
        
        AuditLog.objects.create(
            action_type=action_type,
            description=desc
        )

@receiver(post_delete)
def auto_log_model_delete(sender, instance, **kwargs):
    """Automatically log DELETE events across monitored models."""
    app_label = sender._meta.app_label
    if app_label in MONITORED_APPS and sender != AuditLog:
        model_name = sender._meta.verbose_name.title()
        AuditLog.objects.create(
            action_type="DELETE",
            description=f"Deleted {model_name}: {instance}"
        )

@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    ip = request.META.get('REMOTE_ADDR') if request else None
    AuditLog.objects.create(
        actor=user,
        action_type="LOGIN",
        description=f"User {user.username} ({user.get_full_name() or 'Staff'}) established session.",
        ip_address=ip
    )

@receiver(user_logged_out)
def log_user_logout(sender, request, user, **kwargs):
    if user:
        ip = request.META.get('REMOTE_ADDR') if request else None
        AuditLog.objects.create(
            actor=user,
            action_type="LOGOUT",
            description=f"User {user.username} logged out.",
            ip_address=ip
        )
