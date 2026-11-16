"""Disposable loopback-only QA server for the phone-calls page.

LiveKit is replaced by a simulated gateway, and every telephony setting is a
fake value set before Django loads .env.local, so this server cannot place a
real call. Never use it for normal operation or deployment.
"""

import os
import time
from pathlib import Path
from typing import ClassVar

QA_ENV = {
    "DJANGO_SETTINGS_MODULE": "tests.django_settings",
    "LIVEKIT_URL": "wss://qa.invalid",
    "LIVEKIT_API_KEY": "qa-key",
    "LIVEKIT_API_SECRET": "qa-secret-qa-secret-qa-secret-qa-secret",
    "SIP_OUTBOUND_ADDRESS": "qa.sip.invalid",
    "SIP_OUTBOUND_AUTH_USERNAME": "qaoutbound",
    "SIP_OUTBOUND_AUTH_PASSWORD": "qa-password-not-real",
    "SIP_OUTBOUND_CALLER_ID": "+918000000002",
    "OUTBOUND_TEST_ALLOWED_NUMBER": "+919000000001",
    "SIP_OUTBOUND_TRANSPORT": "tls",
    "SIP_OUTBOUND_MEDIA_ENCRYPTION": "require",
    "TELEPHONY_CONFIRMED": "provider_outbound_ready",
}
QA_CALL_EVENTS_SECRET = "qa-call-events-secret-not-for-production-0123"
os.environ.update(QA_ENV)

import django  # noqa: E402
from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402

database = Path(".test-tmp/calls-browser.sqlite3").resolve()
database.unlink(missing_ok=True)
settings.DEBUG = True
settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
settings.DATABASES["default"]["NAME"] = str(database)
settings.CALL_EVENTS_SECRET = QA_CALL_EVENTS_SECRET
django.setup()
call_command("migrate", verbosity=0)

from django.contrib.auth import get_user_model  # noqa: E402

from apps.telephony import calls, outbound  # noqa: E402

User = get_user_model()
staff, _ = User.objects.get_or_create(
    username="calls-review", defaults={"is_staff": True}
)
staff.is_staff = True
staff.set_password("local-disposable-calls-check")
staff.save()
plain, _ = User.objects.get_or_create(username="plain-review")
plain.set_password("local-disposable-plain-check")
plain.save()


class SimulatedGateway:
    """Dialing for 2 s, ringing until 4.5 s, then answered until the room is
    deleted. Outcomes other than that arrive as signed worker reports."""

    started: ClassVar[dict[str, float]] = {}
    deleted: ClassVar[set[str]] = set()

    def __init__(self) -> None:
        settings_ = outbound.load_outbound_settings()
        plan = outbound.build_outbound_plan(settings_)
        self.trunk = outbound.outbound_trunk_request(plan, settings_).trunk
        self.trunk.sip_trunk_id = "ST_qa"

    async def trunks(self):
        return [self.trunk]

    async def active_test_rooms(self):
        return []

    async def dispatch(self, request):
        SimulatedGateway.started[request.room] = time.monotonic()

    async def call_state(self, room):
        if room in SimulatedGateway.deleted or room not in SimulatedGateway.started:
            return False, "", False
        age = time.monotonic() - SimulatedGateway.started[room]
        if age < 2.0:
            return True, "dialing", True
        if age < 4.5:
            return True, "ringing", True
        return True, "active", True

    async def delete_room(self, room):
        SimulatedGateway.deleted.add(room)


calls.make_gateway = SimulatedGateway
call_command("runserver", "127.0.0.1:8766", use_reloader=False)
