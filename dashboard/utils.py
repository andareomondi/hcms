from .models import AuditLog


def create_audit_entry(actor, action_type, description, ip_address=None):
    """Utility function to explicitly log custom user actions across views."""
    AuditLog.objects.create(
        actor=actor if (actor and actor.is_authenticated) else None,
        action_type=action_type,
        description=description,
        ip_address=ip_address
    )


def log_action(request, action_type, description, actor=None):
    """Shortcut for views: records the requesting user and IP automatically."""
    create_audit_entry(
        actor=actor or request.user,
        action_type=action_type,
        description=description,
        ip_address=request.META.get("REMOTE_ADDR"),
    )
