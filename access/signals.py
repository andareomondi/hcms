from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from dashboard.audit_actions import AuditAction

from . import services


@receiver(user_logged_in)
def _logged_in(sender, request, user, **kwargs):
    services.audit(AuditAction.LOGIN_SUCCEEDED, "Signed in.", actor=user, ip=services.client_ip(request))


@receiver(user_logged_out)
def _logged_out(sender, request, user, **kwargs):
    if user is not None:
        services.audit(AuditAction.LOGOUT, "Signed out.", actor=user,
                       ip=services.client_ip(request) if request else None)


@receiver(user_login_failed)
def _login_failed(sender, credentials, request=None, **kwargs):
    username = (credentials or {}).get("username", "")
    ip = services.client_ip(request) if request is not None else None
    services.record_failed_password(username=username, ip=ip)
