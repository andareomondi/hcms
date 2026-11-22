"""
Browser authorisation and one-time codes.

A staff member can only use the system from a browser an administrator has approved. The browser
holds a random token in a hardened cookie; only a keyed hash of that token is stored here.
"""
from django.conf import settings
from django.db import models


class AuthorisedBrowser(models.Model):
    PENDING = "PENDING"            # requested by a user, waiting for an admin
    AWAITING_CODE = "AWAITING_CODE"  # approved; the one-time code has not yet been entered
    ACTIVE = "ACTIVE"
    LOCKED = "LOCKED"              # too many failed OTP attempts; an admin must unlock
    REVOKED = "REVOKED"
    STATUS_CHOICES = [
        (PENDING, "Waiting for approval"),
        (AWAITING_CODE, "Approved, waiting for code"),
        (ACTIVE, "Active"),
        (LOCKED, "Locked after failed codes"),
        (REVOKED, "Revoked"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="authorised_browsers")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    token_hash = models.CharField(max_length=64, blank=True, db_index=True)
    redeem_hash = models.CharField(max_length=64, blank=True)
    redeem_expires_at = models.DateTimeField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    enrolled_ip = models.GenericIPAddressField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="+")
    public_key_jwk = models.TextField(blank=True)  # the browser's P-256 public key, set at redemption
    trusted_until = models.DateTimeField(null=True, blank=True)  # OTP skipped until this time
    otp_failures = models.PositiveSmallIntegerField(default=0)
    is_emergency = models.BooleanField(default=False)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"{self.user} ({self.get_status_display()})"


class EmailOTP(models.Model):
    LOGIN = "LOGIN"
    EMERGENCY = "EMERGENCY"
    PURPOSE_CHOICES = [(LOGIN, "Login"), (EMERGENCY, "Emergency access")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    browser = models.ForeignKey(AuthorisedBrowser, on_delete=models.CASCADE, null=True, blank=True,
                                related_name="otps")
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default=LOGIN)
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)


class EmergencyAllowance(models.Model):
    """How many emergency passes an admin may issue before a developer resets the allowance."""
    MAX_PER_CYCLE = 3

    admin = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="emergency_allowance")
    uses_used = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class EmergencyPass(models.Model):
    admin = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    token_hash = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)


class OTPEnrolment(models.Model):
    """Exists once a user has confirmed their email code at first login. Until then, enrolment is forced."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="otp_enrolment")
    confirmed_at = models.DateTimeField()


class BackupCode(models.Model):
    """One-time recovery codes for a user who cannot receive email. Only hashes are stored."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="backup_codes")
    code_hash = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)
