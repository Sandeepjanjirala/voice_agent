"""Django settings for the test suite.

The environment is pinned before the real settings load, so tests never depend
on (or touch) a developer's .env.local or a real database.
"""

import os

os.environ["DJANGO_DEBUG"] = "false"
os.environ["DJANGO_SECRET_KEY"] = "test-only-secret-key"
os.environ["DJANGO_ALLOWED_HOSTS"] = "testserver"
# The test client and the browser QA servers speak plain HTTP on this machine.
os.environ["DJANGO_SSL_REDIRECT"] = "false"
os.environ["DJANGO_SECURE_COOKIES"] = "false"
# Set TEST_DATABASE_URL=postgres://... to run the suite against PostgreSQL.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or "sqlite://:memory:"
os.environ["CALL_EVENTS_SECRET"] = ""
# Jobs run in-process in tests; nothing needs Redis.
os.environ["REDIS_URL"] = ""
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
# Throwaway keys for stored contacts; never used outside the tests.
os.environ["CONTACT_ENCRYPTION_KEYS"] = "x2k1-oZ9bXKQ3kJ6m0Qy8v5wD4nL7pR1sT2uV3wX4yY="
os.environ["CONTACT_LOOKUP_KEY"] = "test-only-lookup-key-0123456789abcdef"

from config.settings import *  # noqa: F403

# Tests create many users; real password hashing would dominate the run time.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
