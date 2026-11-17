"""Disposable loopback-only QA server for the dashboard, the Hire wizard and
the leads, contacts and reports pages.

The LLM is replaced by a canned answer and every credential and key is a fake
value set before Django loads .env.local, so this server cannot reach a real
LLM, LiveKit or a phone. Every person and number in it is made up. Never use
it for normal operation or deployment.
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
    "LLM_PROVIDER": "livekit",
    "LLM_MODEL": "",
    "LLM_BASE_URL": "",
    "LLM_API_KEY": "",
    "OPENAI_API_KEY": "",
    "EMPLOYEE_DRAFT_MODEL": "",
    # A phone line that exists only in this file: see SimulatedGateway below.
    "SIP_OUTBOUND_ADDRESS": "qa.sip.invalid",
    "SIP_OUTBOUND_AUTH_USERNAME": "qaoutbound",
    "SIP_OUTBOUND_AUTH_PASSWORD": "qa-password-not-real",
    "SIP_OUTBOUND_CALLER_ID": "+918000000002",
    "OUTBOUND_TEST_ALLOWED_NUMBER": "+919000000001",
    "SIP_OUTBOUND_TRANSPORT": "tls",
    "SIP_OUTBOUND_MEDIA_ENCRYPTION": "require",
    "TELEPHONY_CONFIRMED": "provider_outbound_ready",
}
os.environ.update(QA_ENV)

import django  # noqa: E402
from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402

database = Path(".test-tmp/dashboard-browser.sqlite3").resolve()
database.parent.mkdir(exist_ok=True)
database.unlink(missing_ok=True)
settings.DEBUG = True
settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
# Lets the browser test post signed worker reports, as the voice worker would.
settings.CALL_EVENTS_SECRET = "qa-call-events-secret-not-for-production-0123"
settings.DATABASES["default"]["NAME"] = str(database)
django.setup()
call_command("migrate", verbosity=0)

from django.contrib.auth import get_user_model  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.telephony import calls, outbound  # noqa: E402
from apps.telephony.models import CallRecord  # noqa: E402
from apps.tenancy.models import (  # noqa: E402
    Campus,
    Membership,
    Organisation,
    RetentionPolicy,
)
from apps.workforce import llm, views  # noqa: E402

User = get_user_model()
staff, _ = User.objects.get_or_create(
    username="dashboard-review", defaults={"is_staff": True}
)
staff.is_staff = True
staff.set_password("local-disposable-dashboard-check")
staff.save()
plain, _ = User.objects.get_or_create(username="plain-review")
plain.set_password("local-disposable-plain-check")
plain.save()

# One organisation with two campuses. The reviewer is its admin (and Django
# staff, to reach the platform's own pages); north-review is campus staff.
organisation = Organisation.objects.create(name="Demo Organisation")
RetentionPolicy.objects.create(organisation=organisation)
north = Campus.objects.create(organisation=organisation, name="North Campus")
south = Campus.objects.create(organisation=organisation, name="South Campus")
Membership.objects.create(
    user=staff, organisation=organisation, role=Membership.Role.ORG_ADMIN
)
campus_staff = User.objects.create_user(
    "north-review", password="local-disposable-north-check"
)
Membership.objects.create(
    user=campus_staff, organisation=organisation, role=Membership.Role.CAMPUS_STAFF
).campuses.set([north])
now = timezone.now()
CallRecord.objects.create(
    organisation=organisation,
    campus=north,
    room="qa-answered",
    masked_number="+91********01",
    status="completed",
    answered_at=now,
    ended_at=now,
    duration_seconds=84,
    agent_turns=6,
    user_turns=5,
    e2e_latency_p50=1.3,
    e2e_latency_max=2.1,
    placed_by=staff,
)
CallRecord.objects.create(
    organisation=organisation,
    campus=north,
    room="qa-no-answer",
    masked_number="+91********02",
    status="no_answer",
    failure_reason="Nobody answered before the ringing timeout.",
)
CallRecord.objects.create(
    organisation=organisation,
    campus=south,
    room="qa-south",
    masked_number="+91********03",
    status="failed",
)


CANNED_ANALYSIS = {
    "summary": "The parent asked about a campus visit and wants a call back on "
    "Friday evening.",
    "outcome": "callback",
    "answers": {"class": "Class 6"},
    "callback_requested": True,
    "callback_when": "Friday evening",
    "opt_out_requested": False,
}


def canned_draft(system, user, **kwargs):
    """What a good LLM answer looks like, after a short realistic wait."""
    time.sleep(0.8)
    if system.startswith("You review phone calls"):
        return dict(CANNED_ANALYSIS)
    return {
        "agent_name": "Meera",
        "role": "Admissions enquiry assistant",
        "greeting": (
            "హలో అండి! నేను Meera, AI assistant ని మాట్లాడుతున్నాను. మీ admission "
            "enquiry గురించి call చేశాను. ఒక్క నిమిషం మాట్లాడవచ్చా అండి?"
        ),
        "purpose": "Follow up on the parent's admission enquiry and offer a visit.",
        "steps": [
            {"name": "Confirm parent", "guidance": "Check you are speaking to them."},
            {"name": "Ask class", "guidance": "Ask which class the child joins."},
            {"name": "Offer visit", "guidance": "Offer a campus visit."},
        ],
        "questions": ["Class", "Preferred campus"],
        "limits": "You cannot confirm an admission or quote fees.",
        "ending": "End once a visit preference is noted.",
        "strictness": 2,
        "open_questions": ["Fee structure", "Campus visit timings"],
    }


class SimulatedGateway:
    """Stands in for LiveKit: dialling for 2 s, ringing until 4.5 s, then
    answered until the room is deleted. Nothing leaves this process."""

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
llm.complete_json = canned_draft
views.DRAFT_THROTTLE_SECONDS = 0
call_command("runserver", "127.0.0.1:8767", use_reloader=False)
