"""Disposable loopback-only QA server; never use this launcher in production."""

import os
from pathlib import Path

os.environ["DJANGO_SETTINGS_MODULE"] = "tests.django_settings"

import django
from django.conf import settings
from django.core.management import call_command

settings.DEBUG = True
settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
settings.DATABASES["default"]["NAME"] = str(Path(".test-tmp/browser.sqlite3").resolve())
django.setup()
call_command("migrate", verbosity=0)

from django.contrib.auth import get_user_model  # noqa: E402

user, _ = get_user_model().objects.get_or_create(username="browser-review")
user.set_password("local-disposable-browser-check")
user.save()
call_command("runserver", "127.0.0.1:8765", use_reloader=False)
