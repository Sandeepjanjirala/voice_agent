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

# The test settings set SKIP_ENV_LOCAL, so a developer's real keys, numbers and
# addresses never reach the test suite or the browser QA servers.
if not os.environ.get("SKIP_ENV_LOCAL", "").strip():
    load_dotenv(BASE_DIR / ".env.local")


def _env_bool(name: str, default: bool) -> bool:
    """An empty value means "not set": `NAME=` in .env.local keeps the default."""
    value = os.environ.get(name, "").strip().lower()
    return default if not value else value in ("1", "true", "yes")


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

# --- HTTPS and cookies ------------------------------------------------------------
# With DJANGO_DEBUG=false the site is HTTPS only: plain requests are redirected and
# cookies are sent over HTTPS alone. The health checks stay reachable over plain
# HTTP so a container or load balancer can probe them. Behind a proxy that ends
# TLS, set DJANGO_BEHIND_PROXY=true only behind the supplied trusted proxy.
# The shared VPS binds Gunicorn to host loopback; Nginx replaces forwarded
# headers instead of passing client-supplied values through to Django.
SECURE_SSL_REDIRECT = _env_bool("DJANGO_SSL_REDIRECT", not DEBUG)
SECURE_REDIRECT_EXEMPT = [r"^api/health/"]
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = _env_bool(
    "DJANGO_SECURE_COOKIES", not DEBUG
)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = CSRF_COOKIE_SAMESITE = "Lax"
try:
    SESSION_COOKIE_AGE = int(os.environ.get("DJANGO_SESSION_HOURS", "12")) * 3600
    SECURE_HSTS_SECONDS = int(
        os.environ.get("DJANGO_HSTS_SECONDS", "0" if DEBUG else "3600")
    )
except ValueError as error:
    raise ImproperlyConfigured(
        "DJANGO_SESSION_HOURS and DJANGO_HSTS_SECONDS must be whole numbers."
    ) from error
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
CSRF_TRUSTED_ORIGINS = _env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
if _env_bool("DJANGO_BEHIND_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# HSTS preload is a long-lived commitment for the whole domain; decide at go-live.
SILENCED_SYSTEM_CHECKS = ["security.W021"]

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
    "apps.calling",
    "apps.operations",
    "apps.integrations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.DevAutoLoginMiddleware",
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
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
# Local development only: requests from this computer are signed in as this
# user, with no login page (apps/core/middleware.py). Needs DJANGO_DEBUG=true.
DEV_AUTO_LOGIN_USER = os.environ.get("DEV_AUTO_LOGIN_USER", "").strip()
if DEV_AUTO_LOGIN_USER and not DEBUG:
    raise ImproperlyConfigured(
        "DEV_AUTO_LOGIN_USER is for local development only: it needs "
        "DJANGO_DEBUG=true. Remove it on a deployed server."
    )

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
# Call recordings (apps/calling/recordings.py). Every file is encrypted with
# these keys (Fernet; the first encrypts, all decrypt) before it is stored,
# locally or in S3, so the storage alone never holds playable audio.
RECORDING_ENCRYPTION_KEYS = _env_list("RECORDING_ENCRYPTION_KEYS")
# "local": a private folder on this server, for development and single-server
# installations. "s3": a private S3-compatible bucket (in India: ap-south-1).
RECORDING_STORAGE = os.environ.get("RECORDING_STORAGE", "").strip().lower() or "local"
if RECORDING_STORAGE not in ("local", "s3"):
    raise ImproperlyConfigured("RECORDING_STORAGE must be local or s3.")
RECORDING_LOCAL_DIR = Path(
    os.environ.get("RECORDING_LOCAL_DIR", "").strip() or BASE_DIR / "private_recordings"
)
RECORDING_S3_BUCKET = os.environ.get("RECORDING_S3_BUCKET", "").strip()
RECORDING_S3_REGION = os.environ.get("RECORDING_S3_REGION", "").strip() or "ap-south-1"
# Only for S3-compatible services other than AWS; blank uses AWS.
RECORDING_S3_ENDPOINT = os.environ.get("RECORDING_S3_ENDPOINT", "").strip()
RECORDING_S3_ACCESS_KEY_ID = os.environ.get("RECORDING_S3_ACCESS_KEY_ID", "").strip()
RECORDING_S3_SECRET_ACCESS_KEY = os.environ.get(
    "RECORDING_S3_SECRET_ACCESS_KEY", ""
).strip()
# Blank: S3-managed encryption (SSE-S3). A KMS key ID or ARN: SSE-KMS.
RECORDING_S3_KMS_KEY_ID = os.environ.get("RECORDING_S3_KMS_KEY_ID", "").strip()
# Decrypting a stored contact's number in order to dial it. Off on purpose:
# Stored-contact dialing remains separately gated; outbound destinations also use the dialing policy.
CONTACT_DIALING_ENABLED = _env_bool("CONTACT_DIALING_ENABLED", False)

# --- Outside requests (apps/integrations/transport.py, safety.py) ---------------
# Development only: allow http:// and private or loopback hosts. Never on in
# production.
INTEGRATIONS_ALLOW_LOCAL = DEBUG and _env_bool("INTEGRATIONS_ALLOW_LOCAL", False)
# Bytes an AI employee's instructions may take together (purpose, steps, limits,
# ending), counted as UTF-8: a Telugu letter takes three. They are sent to the
# LLM on every turn, so the size is bounded; 6144 holds about 2000 Telugu
# letters. The worker's AGENT_PROFILE_BUDGET_BYTES must be at least this + 2048.
try:
    EMPLOYEE_INSTRUCTION_BYTES = int(
        os.environ.get("EMPLOYEE_INSTRUCTION_BYTES", "") or 6144
    )
except ValueError as error:
    raise ImproperlyConfigured(
        "EMPLOYEE_INSTRUCTION_BYTES must be a number."
    ) from error
if not 800 <= EMPLOYEE_INSTRUCTION_BYTES <= 16384:
    raise ImproperlyConfigured("EMPLOYEE_INSTRUCTION_BYTES must be 800 to 16384.")
# Phone calls carry only a signed reference to their snapshot in LiveKit job
# metadata; the worker fetches the profile from /api/telephony/call-profile/.
# On by default when the worker can reach Django (CALL_EVENTS_URL is set).
CALL_PROFILE_BY_REFERENCE = _env_bool(
    "CALL_PROFILE_BY_REFERENCE",
    bool(os.environ.get("CALL_EVENTS_URL") or os.environ.get("CALL_PROFILE_URL")),
)
# Voice previews in the employee editor call Sarvam's text-to-speech API.
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "").strip()

# --- Background jobs (config/celery.py) -----------------------------------------
# Redis is the Celery broker and, when set, the shared cache (rate limits must
# hold across processes). Without REDIS_URL the cache is per process and jobs
# can only run with CELERY_TASK_ALWAYS_EAGER, which is for development only.
REDIS_URL = os.environ.get("REDIS_URL", "").strip()
CELERY_BROKER_URL = REDIS_URL or "redis://127.0.0.1:6379/0"
CELERY_RESULT_BACKEND = None
# Without REDIS_URL there is no queue this project's worker reads, so jobs
# (call summaries, call lists) run inside Django instead. Otherwise they would go
# to whatever Redis listens on the default port, and never run.
CELERY_TASK_ALWAYS_EAGER = _env_bool("CELERY_TASK_ALWAYS_EAGER", not REDIS_URL)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BEAT_SCHEDULE = {
    "deployment-heartbeat": {
        "task": "operations.deployment_heartbeat",
        "schedule": 30.0,
        "options": {"expires": 60},
    },
    "analyse-calls": {"task": "operations.analyse_calls", "schedule": 120.0},
    "advance-call-lists": {"task": "operations.advance_call_lists", "schedule": 15.0},
    "follow-up-reminders": {
        "task": "operations.follow_up_reminders",
        "schedule": 900.0,
    },
    "usage-report": {"task": "operations.usage_report", "schedule": 3600.0},
    "retention": {"task": "operations.retention", "schedule": 86400.0},
}
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": "telugu-agent",
        }
    }

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

# Dashboard-only Gemini assistant; never sent to the browser or voice worker.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_ASSISTANT_MODEL = (
    os.environ.get("GEMINI_ASSISTANT_MODEL", "").strip() or "gemini-3.1-flash-lite"
)
