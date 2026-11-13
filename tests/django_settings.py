"""Django settings for the test suite.

The environment is pinned before the real settings load, so tests never depend
on (or touch) a developer's .env.local or a real database.
"""

import os

os.environ["DJANGO_DEBUG"] = "false"
os.environ["DJANGO_SECRET_KEY"] = "test-only-secret-key"
os.environ["DJANGO_ALLOWED_HOSTS"] = "testserver"
os.environ["DATABASE_URL"] = "sqlite://:memory:"

from config.settings import *  # noqa: F403
