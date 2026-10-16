import uuid
from django.conf import settings
from django.db import models

class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs")
    action_type = models.CharField(max_length=50)  # e.g., SIGNUP, CREATE, UPDATE, DELETE, LOGIN, DISCHARGE
    description = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        actor_name = self.actor.username if self.actor else "System/Anonymous"
        timestamp_str = self.timestamp.strftime('%Y-%m-%d %H:%M') if self.timestamp else ""
        return f"[{timestamp_str}] {actor_name} - {self.action_type}"
