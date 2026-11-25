"""Django settings for the test suite.

The environment is pinned before the real settings load, so tests never depend
on (or touch) a developer's .env.local or a real database.
"""

import os

# Never read the developer's .env.local (config/settings.py, voice_agent/agent.py):
# its real LiveKit keys, numbers and addresses must not reach a test. Everything
# a test needs is set here, by its fixtures, or by the browser QA server.
os.environ["SKIP_ENV_LOCAL"] = "1"
os.environ["DJANGO_DEBUG"] = "false"
os.environ["DJANGO_SECRET_KEY"] = "test-only-secret-key"
os.environ["DJANGO_ALLOWED_HOSTS"] = "testserver"
# The test client and the browser QA servers speak plain HTTP on this machine.
os.environ["DJANGO_SSL_REDIRECT"] = "false"
os.environ["DJANGO_SECURE_COOKIES"] = "false"
# Set TEST_DATABASE_URL=postgres://... to run the suite against PostgreSQL.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or "sqlite://:memory:"
os.environ["CALL_EVENTS_SECRET"] = ""
# Dialing and call-profile settings are pinned too: a developer's .env.local
# (live dialing, a CALL_EVENTS_URL) must never change what the tests see.
os.environ["OUTBOUND_DIALING_MODE"] = "allowlist"
os.environ.pop("OUTBOUND_ALLOWLIST_NUMBERS", None)
os.environ["CONTACT_DIALING_ENABLED"] = "false"
os.environ["CALL_PROFILE_BY_REFERENCE"] = "false"
os.environ.pop("CALL_EVENTS_URL", None)
os.environ.pop("CALL_PROFILE_URL", None)
os.environ.pop("EMPLOYEE_INSTRUCTION_BYTES", None)
os.environ.pop("DEV_AUTO_LOGIN_USER", None)
# Jobs run in-process in tests; nothing needs Redis.
os.environ["REDIS_URL"] = ""
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
# Throwaway keys for stored contacts; never used outside the tests.
os.environ["CONTACT_ENCRYPTION_KEYS"] = "x2k1-oZ9bXKQ3kJ6m0Qy8v5wD4nL7pR1sT2uV3wX4yY="
os.environ["CONTACT_LOOKUP_KEY"] = "test-only-lookup-key-0123456789abcdef"

from config.settings import *  # noqa: F403

# Tests create many users; real password hashing would dominate the run time.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
