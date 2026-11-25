"""The whole platform workflow through the dashboard, as an admin uses it:

Hire an employee -> write long Telugu instructions -> add knowledge ->
publish -> assign a campus -> add a business line -> Make calls: type a
number, check it, confirm consent and start -> the voice worker fetches the
call's immutable profile by signed reference and reports the call -> the
result is analysed and shown -> the line answers an inbound call.

Every step is an HTTP request to the Django interface, except the voice
worker's own signed requests, which are made as the worker makes them.
LiveKit is the fake gateway and the LLM a canned answer: no call, no network.
"""

# The fixtures imported from other test modules are named as test parameters.
# ruff: noqa: F811

import json
import time
from datetime import datetime
from datetime import time as clock

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.calling import analysis
from apps.calling.models import CallList, CallResult, PhoneNumber
from apps.integrations import transport
from apps.telephony.models import CallRecord
from apps.workforce import llm
from apps.workforce.models import Employee, KnowledgeItem
from voice_agent.call_reporting import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign
from voice_agent.profile import PROFILE_REF_KEY, parse_profile

from .test_calling import SCRIPT, TRANSCRIPT, canned, consented, inbound_post
from .test_calls import SECRET, env, gateway, post_event  # noqa: F401
from .test_tenancy_crm import ASHA, World, assert_no_full_number, body

pytestmark = pytest.mark.django_db

LINE = "+914012345690"
TELUGU_PURPOSE = (
    "తల్లిదండ్రులు పంపిన admission enquiry గురించి మాట్లాడి, వారి పిల్లల తరగతి, "
    "ఇష్టమైన campus, మరియు campus visit కి అనువైన సమయం తెలుసుకోండి. "
) * 3


def worker_post(name, payload):
    """A request signed with CALL_EVENTS_SECRET, as the voice worker sends it."""
    raw = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    return Client().post(
        reverse(f"telephony:{name}"),
        raw,
        content_type="application/json",
        **{
            "HTTP_" + TIMESTAMP_HEADER.upper().replace("-", "_"): timestamp,
            "HTTP_" + SIGNATURE_HEADER.upper().replace("-", "_"): sign(
                SECRET, timestamp, raw
            ),
        },
    )


def test_hire_to_result_through_the_dashboard(env, gateway, monkeypatch, settings):
    # The gateway is a fake, so nothing leaves the machine.
    settings.CALL_PROFILE_BY_REFERENCE = True
    env.setenv("OUTBOUND_DIALING_MODE", "live")
    eleven = timezone.make_aware(datetime.combine(timezone.localdate(), clock(11)))
    monkeypatch.setattr(timezone, "now", lambda: eleven)
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: dict(SCRIPT))

    def no_network(*args, **kwargs):
        raise AssertionError("no provider may be contacted")

    monkeypatch.setattr(transport, "send", no_network)
    world = World()
    world.org_a.transcripts = "keep"
    world.org_a.save()
    admin = world.client(world.admin_a)

    # 1. Hire.
    hired = admin.post(
        reverse("employee-hire"),
        {
            "brief": "Follow up admission enquiries and offer campus visits.",
            "channel": "instant",
            "language": "te-en",
        },
    )
    assert hired.status_code == 302
    employee = Employee.objects.get()

    # 2. Knowledge for the campus.
    admin.post(
        reverse("knowledge"),
        {
            "kind": "fact",
            "campus": str(world.north.pk),
            "title": "Visits",
            "text": "Visits run 10 to 1 on weekdays.",
            "is_active": "on",
        },
    )
    assert KnowledgeItem.objects.filter(title="Visits").exists()

    # 3. Long Telugu instructions, the campus and the actions; then publish.
    editor = reverse("employee-detail", args=[employee.pk])
    assert 'data-budget-total="6144"' in body(admin.get(editor))
    saved = admin.post(
        editor,
        {
            "name": "Meera",
            "role": "Admission enquiries",
            "channel": "instant",
            "language": "te-en",
            "greeting": "నమస్తే అండి, నేను Meera, AI assistant ని.",
            "purpose": TELUGU_PURPOSE,
            "limits": "ఫీజు లేదా seats గురించి హామీ ఇవ్వవద్దు.",
            "ending": "ధన్యవాదాలు చెప్పి ముగించండి.",
            "strictness": "3",
            "step_name": ["తరగతి", "Visit"],
            "step_guidance": ["పిల్లల తరగతి అడగండి.", "Campus visit కి సమయం అడగండి."],
            "question": ["Class", "Visit day"],
            "campus": [str(world.north.pk)],
            "action": ["create_follow_up"],
        },
        follow=True,
    )
    assert "Draft saved." in body(saved)
    published = admin.post(reverse("employee-publish", args=[employee.pk]), follow=True)
    assert "Version 1 of Meera is published." in body(published)
    employee.refresh_from_db()
    assert employee.live_version.config["purpose"] == " ".join(TELUGU_PURPOSE.split())
    assert employee.live_version.config["actions"] == ["create_follow_up"]
    assert list(employee.campuses.all()) == [world.north]

    # 4. A business line, routed for inbound calls.
    admin.post(
        reverse("numbers"),
        {
            "number": LINE,
            "label": "North admissions",
            "campus": str(world.north.pk),
            "employee": str(employee.pk),
            "routing_enabled": "on",
        },
    )
    line = PhoneNumber.objects.get(number=LINE)
    assert line.employee == employee and line.routing_enabled
    assert line.organisation == world.org_a

    # 5. Make calls: the number is typed, checked, confirmed and called.
    contact = consented(world, world.north, ASHA)
    made = admin.post(
        reverse("make-calls"),
        {
            "speaker": str(employee.pk),
            "campus": str(world.north.pk),
            "purpose": "Campus visit for Class 6",
            "numbers": f"Asha Rao, {ASHA}",
        },
    )
    assert made.status_code == 302
    call_list = CallList.objects.get()
    checked = body(admin.get(reverse("call-list", args=[call_list.pk])))
    assert "Everyone on this list agreed" in checked
    assert_no_full_number(checked)
    admin.post(
        reverse("call-list", args=[call_list.pk]), {"action": "start", "consent": "yes"}
    )
    step = admin.post(reverse("call-list-step", args=[call_list.pk])).json()
    assert step["tally"]["calling"] == 1
    record = call_list.entries.get().call

    # 6. The worker gets a reference, never the instructions, and fetches them.
    (request,) = gateway.dispatches
    metadata = json.loads(request.metadata)
    assert metadata["outbound_call"]["to"] == ASHA
    assert "agent_profile" not in metadata and "Asha" not in request.metadata
    fetched = worker_post("call-profile", metadata[PROFILE_REF_KEY])
    assert fetched.status_code == 200
    profile = parse_profile(json.dumps(fetched.json()))
    assert profile.purpose == " ".join(TELUGU_PURPOSE.split()), "nothing was cut"
    assert profile.caller_name == "Asha Rao"
    assert profile.about == "Campus visit for Class 6"
    assert "Visits run 10 to 1" in profile.business_info

    # 7. The worker reports the call; the result is analysed and shown.
    assert (
        post_event(Client(), {"room": record.room, "event": "answered"}).status_code
        == 204
    )
    ended = post_event(
        Client(),
        {
            "room": record.room,
            "event": "ended",
            "outcome": "completed",
            "talk_seconds": 95,
            "transcript": TRANSCRIPT,
        },
    )
    assert ended.status_code == 204
    record.refresh_from_db()
    assert record.status == "completed"
    analysis.analyse(record, complete=canned())
    assert CallResult.objects.get(call=record).outcome == "callback"
    result_page = body(admin.get(reverse("conversation-detail", args=[record.pk])))
    assert "The parent asked about visiting" in result_page
    assert_no_full_number(result_page)

    # 8. The same line answers an inbound call with the same published version.
    answer = inbound_post({"room": "call-in-1", "called": LINE, "caller": ASHA})
    assert answer.status_code == 200
    inbound = CallRecord.objects.get(room="call-in-1")
    assert (inbound.employee_version, inbound.campus, inbound.contact) == (
        employee.live_version,
        world.north,
        contact,
    )
