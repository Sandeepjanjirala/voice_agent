"""The Celery app: background jobs and the one Beat schedule.

Run alongside Django (never inside the voice worker):

    celery -A config worker --loglevel=info
    celery -A config beat --loglevel=info      # exactly one, ever

Jobs are listed in settings.CELERY_BEAT_SCHEDULE. Each is idempotent and
records its runs (apps/operations), so a retry or an overlap is harmless.
No job dials a phone: real outbound calling stays switched off.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("telugu_agent")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
