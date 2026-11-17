"""Django settings for the calling-agent backend.

Django owns business APIs, persistence, auth and the dashboard. The LiveKit
voice worker (voice_agent/) is a separate process and is never imported here.
"""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env.local")


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes")


def _env_list(name: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


DEBUG = _env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is not set. Copy .env.example to .env.local and "
            "fill it in, or set DJANGO_DEBUG=true for local development."
        )
    SECRET_KEY = "insecure-local-development-key-do-not-use-in-production"

ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS")
if DEBUG and not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.core",
    "apps.telephony",
    "apps.tenancy",
    "apps.workforce",
    "apps.crm",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.navigation.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# PostgreSQL is the target database (set DATABASE_URL=postgres://...). SQLite is
# only a zero-setup fallback for the Phase 1 skeleton, which has no models yet.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{(BASE_DIR / 'db.sqlite3').as_posix()}",
        conn_max_age=60,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

# Server-only credentials; never included in template context.
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "").strip()
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "").strip()
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "").strip()
LIVEKIT_AGENT_NAME = (
    os.environ.get("AGENT_NAME", "telugu-agent").strip() or "telugu-agent"
)

# Shared with the voice worker, which signs call-outcome reports with it.
CALL_EVENTS_SECRET = os.environ.get("CALL_EVENTS_SECRET", "").strip()

# Drafting AI employees uses the same LLM settings as the voice worker.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "").strip() or "livekit"
LLM_MODEL = os.environ.get("LLM_MODEL", "").strip()
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "").strip()
LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
LIVEKIT_INFERENCE_URL = os.environ.get("LIVEKIT_INFERENCE_URL", "").strip()
# Optional: a different model for drafting than for calls.
EMPLOYEE_DRAFT_MODEL = os.environ.get("EMPLOYEE_DRAFT_MODEL", "").strip()

# Stored phone numbers (apps/crm/crypto.py). Newest encryption key first.
CONTACT_ENCRYPTION_KEYS = _env_list("CONTACT_ENCRYPTION_KEYS")
CONTACT_LOOKUP_KEY = os.environ.get("CONTACT_LOOKUP_KEY", "").strip()
# Decrypting a stored contact's number in order to dial it. Off on purpose:
# until wider calling is authorised, calls go only to OUTBOUND_TEST_ALLOWED_NUMBER.
CONTACT_DIALING_ENABLED = False

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Closed by default: every API view must opt in to wider access explicitly.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO").upper(),
    },
}
