"""Django settings for the test suite.

The environment is pinned before the real settings load, so tests never depend
on (or touch) a developer's .env.local or a real database.
"""

import os

os.environ["DJANGO_DEBUG"] = "false"
os.environ["DJANGO_SECRET_KEY"] = "test-only-secret-key"
os.environ["DJANGO_ALLOWED_HOSTS"] = "testserver"
# Set TEST_DATABASE_URL=postgres://... to run the suite against PostgreSQL.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or "sqlite://:memory:"
os.environ["CALL_EVENTS_SECRET"] = ""

from config.settings import *  # noqa: F403
