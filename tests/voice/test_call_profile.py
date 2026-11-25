"""A phone call's employee profile by signed reference, and the size budget
of the per-call prompt section, on the worker's side. No network: the HTTP
session is the fake from test_employee_calls."""

import json

import pytest

from voice_agent import call_profile, outbound
from voice_agent.call_reporting import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign
from voice_agent.config import ConfigError
from voice_agent.profile import (
    PROFILE_BUDGET_BYTES,
    parse_profile,
    parse_profile_ref,
    profile_instructions,
)

from .fakes import make_settings
from .test_employee_calls import SECRET, FakeSession
from .test_outbound_worker import MOBILE, FakeJob, metadata

EVENTS = "http://127.0.0.1:8000/api/telephony/call-events/"
REF = {
    "call": "4f3c2a1e-0000-4000-8000-000000000001",
    "expires": "1999999999",
    "token": "t" * 64,
}


@pytest.fixture(autouse=True)
def fresh_session():
    FakeSession.sent = []
    FakeSession.status, FakeSession.payload, FakeSession.error = 200, {}, None


def settings(**env):
    return make_settings(CALL_EVENTS_SECRET=SECRET, CALL_EVENTS_URL=EVENTS, **env)


def test_the_profile_address_sits_next_to_the_events_address():
    assert settings().call_profile_url == (
        "http://127.0.0.1:8000/api/telephony/call-profile/"
    )
    explicit = settings(CALL_PROFILE_URL="https://calls.example.org/p/")
    assert explicit.call_profile_url == "https://calls.example.org/p/"
    with pytest.raises(ConfigError, match="CALL_PROFILE_URL"):
        settings(CALL_PROFILE_URL="ftp://x")
    # Without the secret nothing can be signed, so nothing is fetched.
    assert make_settings(CALL_EVENTS_URL=EVENTS).call_profile_url is None


def test_the_budget_is_a_validated_setting():
    assert settings().profile_budget_bytes == PROFILE_BUDGET_BYTES == 8192
    assert settings(AGENT_PROFILE_BUDGET_BYTES="12000").profile_budget_bytes == 12000
    with pytest.raises(ConfigError):
        settings(AGENT_PROFILE_BUDGET_BYTES="100")


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "{}",
        '{"agent_profile_ref": 1}',
        json.dumps({"agent_profile_ref": {"call": "x"}}),
    ],
)
def test_incomplete_references_are_ignored(raw):
    assert parse_profile_ref(raw) is None


async def test_the_profile_is_fetched_with_a_signed_request():
    FakeSession.payload = {
        "agent_profile": {
            "purpose": "Follow up.",
            "greeting": "నమస్తే.",
            "voice": {"speaker": "kavya"},
        }
    }
    ref = parse_profile_ref(json.dumps({"agent_profile_ref": REF}))
    profile = await call_profile.fetch_call_profile(
        settings(), ref, session_factory=FakeSession
    )
    assert profile.greeting == "నమస్తే." and profile.speaker == "kavya"
    (sent,) = FakeSession.sent
    assert json.loads(sent["body"]) == REF
    headers = sent["headers"]
    assert headers[SIGNATURE_HEADER] == sign(
        SECRET, headers[TIMESTAMP_HEADER], sent["body"]
    )


@pytest.mark.parametrize(
    ("status", "payload", "error"),
    [(404, {}, None), (403, {}, None), (200, ["x"], None), (200, {}, OSError("down"))],
)
async def test_any_failure_gives_no_profile(status, payload, error):
    FakeSession.status, FakeSession.payload, FakeSession.error = status, payload, error
    assert (
        await call_profile.fetch_call_profile(
            settings(), REF, session_factory=FakeSession
        )
        is None
    )


async def test_without_an_address_nothing_is_asked():
    assert (
        await call_profile.fetch_call_profile(
            make_settings(), REF, session_factory=FakeSession
        )
        is None
    )
    assert FakeSession.sent == []


async def test_a_call_without_its_profile_is_never_dialled(monkeypatch):
    def no_session(*args, **kwargs):
        raise AssertionError("no conversation without the employee's instructions")

    monkeypatch.setattr(outbound, "build_session", no_session)
    job = FakeJob()
    outcome = await outbound.run_outbound_call(
        job,
        make_settings(OUTBOUND_TEST_ALLOWED_NUMBER=MOBILE),
        outbound.parse_dial_request(metadata()),
        None,
        profile_missing=True,
    )
    assert outcome == outbound.REFUSED
    assert job.requests == []
    assert job.shutdown_reasons == ["outbound call refused"]


# --- the per-call prompt section ------------------------------------------------------


def meta(**fields) -> str:
    return json.dumps({"agent_profile": {"greeting": "నమస్తే.", **fields}})


def test_long_telugu_instructions_reach_the_prompt_whole():
    # About 1900 Telugu letters of instructions: 5.7 kB, inside the budget.
    purpose = "విద్యార్థి తల్లిదండ్రులతో admission గురించి మాట్లాడండి. " * 10
    goals = " ".join(f"{n}. తరగతి, campus, visit సమయం అడగండి." for n in range(1, 13))
    limits = "ఫీజు చెప్పవద్దు. admission ఖచ్చితం అని చెప్పవద్దు. " * 6
    profile = parse_profile(
        meta(
            purpose=purpose.strip(),
            objectives=goals,
            limitations=limits.strip(),
            ending="ధన్యవాదాలు చెప్పి ముగించండి.",
        )
    )
    section = profile_instructions(profile, PROFILE_BUDGET_BYTES)
    for value in (
        profile.purpose,
        profile.objectives,
        profile.limitations,
        profile.ending,
    ):
        assert value in section
    assert len(section.encode()) <= PROFILE_BUDGET_BYTES


def test_an_over_budget_section_drops_whole_lines_and_says_so(caplog):
    # Django refuses this at publishing; a worker set lower than Django still
    # never sends half a sentence, and logs what it left out.
    profile = parse_profile(meta(purpose="అ" * 200, objectives="ఆ" * 3000))
    with caplog.at_level("WARNING", logger="voice_agent"):
        section = profile_instructions(profile, 1024)
    assert len(section.encode()) <= 1024
    assert profile.purpose in section and "ఆ" not in section
    assert any("over budget" in record.message for record in caplog.records)


# --- the caller ID chosen for a business call ---------------------------------------


async def test_the_chosen_caller_id_reaches_the_sip_request():
    job = FakeJob()
    dial = outbound.parse_dial_request(metadata(sip_number="+914012345600"))
    result = await outbound.place_call(
        job, dial, make_settings(OUTBOUND_TEST_ALLOWED_NUMBER=MOBILE)
    )
    assert result.outcome == outbound.ANSWERED
    (request,) = job.requests
    assert request.sip_number == "+914012345600"


async def test_a_malformed_caller_id_is_never_dialled():
    job = FakeJob()
    dial = outbound.parse_dial_request(metadata(sip_number="040 1234"))
    result = await outbound.place_call(
        job, dial, make_settings(OUTBOUND_TEST_ALLOWED_NUMBER=MOBILE)
    )
    assert result.outcome == outbound.REFUSED and job.requests == []
