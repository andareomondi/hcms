"""
Django settings for HCMS (Hospital Clinical Management System).
Configured for Django 5.2 LTS and PostgreSQL 18.

All secrets and environment-specific values come from environment variables.
For local development, put them in a .env file at the project root (see .env.example).
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env from the project root, if present. Real environment variables take precedence.
load_dotenv(BASE_DIR / ".env")


def env_required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"Set {name} in your .env file (see .env.example).")
    return value


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def env_list(name):
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env_required("DJANGO_SECRET_KEY")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env_bool("DJANGO_DEBUG", default=False)
ALLOW_STAFF_REGISTRATION = env_bool("ALLOW_STAFF_REGISTRATION", default=False)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Using named urls for login/logout redirects instead of hardcoded paths.
LOGIN_URL = "login"
LOGOUT_REDIRECT_URL = "login"
# Send them to clock in screen
LOGIN_REDIRECT_URL = "duty:clock_in"

# Application definition
INSTALLED_APPS = [
    # Django default apps
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Third-party extensions
    "simple_history",
    "django_otp",
    "django_otp.plugins.otp_totp",

    # HCMS Modular Monolith Apps
    "core_patients",
    "triage_queue",
    "doctors_portal",
    "pharmacy",
    "radiology",
    "wards",
    "billing",
    "dashboard",
    "duty",
    "notifications",
    "maternity",
    "lab",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "hospital_system.middleware.StaffProfileMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    "hospital_system.middleware_htmx.HtmxSessionExpiredMiddleware",
]

ROOT_URLCONF = "hospital_system.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "hospital_system.context_processors.permissions",
                "notifications.context_processors.notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "hospital_system.wsgi.application"

# Database: PostgreSQL. Values come from the DB_* variables in .env.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env_required("DB_NAME"),
        "USER": env_required("DB_USER"),
        "PASSWORD": env_required("DB_PASSWORD"),
        "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

# Password Hashing (Argon2id primary hasher). Requires the argon2-cffi package.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

# Static files & Media
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Data export (dashboard/export.py and the export_all_data command)
EXPORT_MAX_WEB_ROWS = 500_000
EXPORT_DIR = BASE_DIR / "private_exports"   # outside MEDIA_ROOT, never served
EXPORT_MIN_PASSPHRASE = 12
EXPORT_INCLUDE_FILES_DEFAULT = False
