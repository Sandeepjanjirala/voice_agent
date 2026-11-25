"""AI employee instructions: the size budget that replaced the 800-byte limit,
and the signed reference that keeps instructions and caller details out of
LiveKit job metadata. No call leaves the test: LiveKit is the fake gateway."""

# The fixtures imported from test_calls are named as test parameters.
# ruff: noqa: F811

import json
import time

import pytest
from django.urls import reverse

from apps.telephony import calls
from apps.telephony.models import CallRecord
from apps.workforce import config, publishing
from voice_agent.call_reporting import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign
from voice_agent.profile import (
    PROFILE_BUDGET_BYTES,
    PROFILE_REF_KEY,
    parse_profile,
    profile_instructions,
)

from .test_calling import SCRIPT, employee_for
from .test_calls import SECRET, env, gateway  # noqa: F401
from .test_tenancy_crm import World

pytestmark = pytest.mark.django_db


def utf8(text: str) -> int:
    return len(text.encode("utf-8"))


def exactly(total: int, prefix: str = "") -> str:
    """Mixed Telugu/English text of exactly `total` UTF-8 bytes."""
    text = prefix
    while utf8(text) + 3 <= total:
        text += "అ"
    return text + "a" * (total - utf8(text))


# --- the screenshot: 995 of 800 bytes ---------------------------------------------------


def test_an_employee_with_995_bytes_of_instructions_publishes():
    world = World()
    draft = {
        **SCRIPT,
        "purpose": exactly(450, "Follow up the parent's admission enquiry. "),
        "limits": exactly(300, "Do not quote fees. "),
        "ending": exactly(200, "Thank them. "),
    }
    employee = employee_for(world, publish=False, **draft)
    size = publishing.prompt_size(employee.draft)
    assert size > 995 and publishing.problems(employee.draft) == []
    version = publishing.publish(employee, world.admin_a)
    assert version.payload["purpose"] == draft["purpose"]


# --- long and mixed instructions ----------------------------------------------------------


def long_telugu_draft():
    return {
        **SCRIPT,
        "purpose": "విద్యార్థి తల్లిదండ్రులు పంపిన admission enquiry గురించి మాట్లాడి, "
        "వారికి సరైన campus మరియు class వివరాలు అర్థమయ్యేలా చెప్పండి. " * 4,
        "steps": [
            {
                "name": f"దశ {n}",
                "guidance": f"{n}వ దశ: తల్లిదండ్రుల ప్రశ్నను శ్రద్ధగా విని, "
                "అవసరమైతే మళ్ళీ అడిగి, స్పష్టంగా సమాధానం ఇవ్వండి. " * 2,
            }
            for n in range(1, 9)
        ],
        "questions": ["తరగతి", "ఇష్టమైన campus", "visit కి అనువైన రోజు"],
        "limits": "ఫీజు, seats లేదా admission గ్యారంటీ గురించి ఏమీ చెప్పవద్దు. " * 3,
        "ending": "ధన్యవాదాలు చెప్పి, office team call చేస్తుందని చెప్పి ముగించండి.",
    }


def test_long_telugu_instructions_publish_and_reach_the_worker_whole():
    world = World()
    employee = employee_for(world, publish=False, **long_telugu_draft())
    size = publishing.prompt_size(employee.draft)
    assert 3000 < size <= publishing.instruction_budget()
    assert publishing.problems(employee.draft) == []
    publishing.publish(employee, world.admin_a)
    employee.refresh_from_db()
    plan = publishing.plan_call(employee)
    parsed = parse_profile(json.dumps(plan.metadata))
    for field in ("purpose", "objectives", "limitations", "ending"):
        assert getattr(parsed, field) == plan.metadata["agent_profile"][field], field
    section = profile_instructions(parsed, PROFILE_BUDGET_BYTES)
    assert (
        parsed.objectives in section and len(section.encode()) <= PROFILE_BUDGET_BYTES
    )


def test_long_english_instructions_publish():
    world = World()
    draft = {
        **SCRIPT,
        "steps": [
            {
                "name": f"Step {n}",
                "guidance": "Listen carefully, answer briefly and check they understood. "
                * 4,
            }
            for n in range(1, 13)
        ],
    }
    employee = employee_for(world, publish=False, **draft)
    assert publishing.problems(employee.draft) == []
    assert len(config.normalize(employee.draft)["steps"]) == 12


@pytest.mark.parametrize(
    "text",
    [
        "Mixed Telugu, English: తెలుగు and English కలిపి.",
        "Telugu conjuncts and virama: క్ష్మ, శ్రీ, ద్ద్వ.",
        "Emoji and joiners: 👨‍👩‍👧 ✔️ ①.",
        "Devanagari and Tamil: नमस्ते வணக்கம்.",
        "Combining marks: é (e + U+0301), zero width​space.",
    ],
)
def test_bytes_are_counted_as_utf8_for_every_script(text):
    draft = {**SCRIPT, "purpose": text}
    payload = publishing.worker_payload(draft)
    counted = sum(utf8(payload[field]) for field in publishing.PROMPT_FIELDS)
    assert publishing.prompt_size(draft) == counted
    assert config.normalize(draft)["purpose"] == " ".join(text.split())


def test_over_the_budget_explains_by_how_much_and_what_to_do(settings):
    settings.EMPLOYEE_INSTRUCTION_BYTES = 2000
    draft = {**SCRIPT, "purpose": "అ" * 800}  # 2400 bytes on its own
    (reason,) = [r for r in publishing.problems(draft) if "bytes" in r]
    assert "of 2,000 bytes" in reason
    assert "Telugu letters too many" in reason and "Knowledge" in reason


def test_the_editor_shows_live_usage(client):
    world = World()
    employee = employee_for(world, publish=False)
    page = world.client(world.admin_a).get(
        reverse("employee-detail", args=[employee.pk])
    )
    html = page.content.decode()
    assert 'data-budget-total="6144"' in html and "dashboard/budget.js" in html
    assert 'name="purpose" rows="3" data-budget' in html


def test_the_budget_is_configurable_and_validated():
    from django.core.exceptions import ImproperlyConfigured  # noqa: F401

    assert publishing.instruction_budget() == 6144
    # The worker's section must hold the largest Django allows, plus labels.
    assert publishing.instruction_budget() + 2048 <= PROFILE_BUDGET_BYTES


# --- by reference: nothing personal in LiveKit metadata -------------------------------------


@pytest.fixture
def by_reference(env, settings):
    settings.CALL_PROFILE_BY_REFERENCE = True
    return settings


def start_planned_call(world, gateway):
    employee = employee_for(world)
    lead = world.lead(world.admin_a, world.north, subject="Campus visit")
    plan = publishing.plan_call(employee, campus=world.north, lead=lead)
    return calls.start_call(call_plan=plan, campus=world.north, lead=lead), employee


def dispatched_metadata(gateway) -> dict:
    (request,) = gateway.dispatches
    return json.loads(request.metadata)


def worker_post(client, body: dict, *, secret=SECRET, timestamp=None):
    raw = json.dumps(body).encode()
    timestamp = timestamp or str(int(time.time()))
    return client.post(
        reverse("telephony:call-profile"),
        raw,
        content_type="application/json",
        **{
            "HTTP_" + TIMESTAMP_HEADER.upper().replace("-", "_"): timestamp,
            "HTTP_" + SIGNATURE_HEADER.upper().replace("-", "_"): sign(
                secret, timestamp, raw
            ),
        },
    )


def test_a_planned_call_carries_only_a_reference(by_reference, gateway):
    world = World()
    record, _ = start_planned_call(world, gateway)
    metadata = dispatched_metadata(gateway)
    assert "agent_profile" not in metadata
    ref = metadata[PROFILE_REF_KEY]
    assert ref["call"] == str(record.pk)
    text = json.dumps(metadata)
    for private in ("Asha", "Campus visit", "Meera", SCRIPT["purpose"]):
        assert private not in text


def test_the_worker_fetches_the_calls_own_snapshot(by_reference, gateway, client):
    world = World()
    record, employee = start_planned_call(world, gateway)
    ref = dispatched_metadata(gateway)[PROFILE_REF_KEY]
    # A draft edit after the call started must not reach it.
    employee.draft = {**employee.draft, "purpose": "Something else entirely."}
    employee.save()
    response = worker_post(client, ref)
    assert response.status_code == 200
    profile = response.json()["agent_profile"]
    assert profile == record.context_snapshot["profile"]
    assert profile["purpose"] == SCRIPT["purpose"]
    assert profile["context"]["name"] == "Asha Rao"


def test_forged_expired_or_finished_references_are_refused(
    by_reference, gateway, client
):
    world = World()
    record, _ = start_planned_call(world, gateway)
    ref = dispatched_metadata(gateway)[PROFILE_REF_KEY]
    assert worker_post(client, {**ref, "token": "0" * 64}).status_code == 404
    unknown = "00000000-0000-4000-8000-000000000000"
    forged = {"call": unknown, "expires": ref["expires"]}
    forged["token"] = calls._profile_token(unknown, ref["expires"])
    assert worker_post(client, forged).status_code == 404
    assert worker_post(client, ref, secret="w" * 40).status_code == 403
    assert (
        worker_post(client, ref, timestamp=str(int(time.time()) - 3600)).status_code
        == 403
    )
    expired = {**ref, "expires": str(int(time.time()) - 1)}
    expired["token"] = calls._profile_token(ref["call"], expired["expires"])
    assert worker_post(client, expired).status_code == 404
    CallRecord.objects.filter(pk=record.pk).update(status=CallRecord.Status.COMPLETED)
    assert worker_post(client, ref).status_code == 404


def test_without_reporting_the_profile_travels_inline(env, gateway, settings):
    settings.CALL_PROFILE_BY_REFERENCE = False
    world = World()
    start_planned_call(world, gateway)
    metadata = dispatched_metadata(gateway)
    assert PROFILE_REF_KEY not in metadata and "agent_profile" in metadata
