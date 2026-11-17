"""Per-call employee settings in the worker: voice, context, transcript, and the
inbound lookup. No network and no call: sessions and HTTP are fakes."""

import json
from types import SimpleNamespace
from typing import ClassVar

import pytest
from livekit.agents import llm
from livekit.plugins.sarvam.models import MODEL_SPEAKER_COMPATIBILITY

from voice_agent import inbound
from voice_agent.call_reporting import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    TRANSCRIPT_TOTAL_CHARS,
    CallMetrics,
    sign,
)
from voice_agent.config import ConfigError
from voice_agent.profile import parse_profile, profile_instructions, voice_for

from .fakes import make_settings

SECRET = "s" * 40
URL = "http://127.0.0.1:8000/api/telephony/inbound-context/"


def meta(**profile) -> str:
    base = {"purpose": "Follow up on an enquiry.", "greeting": "Hello."}
    return json.dumps({"agent_profile": {**base, **profile}})


# --- voice -------------------------------------------------------------------------


def test_a_profile_chooses_its_own_voice_pace_and_language():
    settings = make_settings()
    profile = parse_profile(
        meta(voice={"speaker": "Rahul", "pace": 1.2, "language": "en-IN"})
    )
    voice = voice_for(settings, profile)
    assert (voice.tts.speaker, voice.tts.pace) == ("rahul", 1.2)
    assert voice.tts.language == voice.stt.language == "en-IN"
    # Everything else, and the original settings, are untouched.
    assert voice.llm == settings.llm and voice.turn == settings.turn
    assert settings.tts.speaker == "kavitha" and settings.tts.language == "te-IN"


def test_bad_voice_choices_fall_back_to_the_configured_voice(caplog):
    settings = make_settings()
    profile = parse_profile(
        meta(voice={"speaker": "nobody", "pace": "fast", "language": "xx-YY"})
    )
    assert (profile.speaker, profile.pace, profile.language) == ("nobody", None, "")
    voice = voice_for(settings, profile)
    assert voice.tts == settings.tts and voice.stt == settings.stt
    assert "profile voice is not available" in caplog.text


@pytest.mark.parametrize("pace, expected", [(9, 2.0), (0.1, 0.5), (1.0, 1.0)])
def test_pace_is_clamped(pace, expected):
    assert parse_profile(meta(voice={"pace": pace})).pace == expected


def test_no_profile_and_no_voice_change_nothing():
    settings = make_settings()
    assert voice_for(settings, None) is settings
    assert voice_for(settings, parse_profile(meta())) == settings


def test_every_voice_django_offers_exists_in_the_worker():
    # Kept in step with apps/workforce/voices.py, which Django uses.
    from apps.workforce.voices import TTS_MODEL, VOICE_MODELS, VOICES

    for voice, _ in VOICES:
        assert voice in MODEL_SPEAKER_COMPATIBILITY[VOICE_MODELS[voice]]["all"]
    assert make_settings().tts.model == TTS_MODEL


def test_approved_v4_voice_and_legacy_employee_voice_select_matching_models():
    settings = make_settings(
        SARVAM_TTS_MODEL="bulbul:v4-flash",
        SARVAM_TTS_SPEAKER="shubh_enhi_ads",
        SARVAM_TTS_PACE="1.0",
        SARVAM_TTS_SAMPLE_RATE="24000",
    )
    approved = voice_for(
        settings, parse_profile(meta(voice={"speaker": "shubh_enhi_ads", "pace": 1.0}))
    )
    assert (
        approved.tts.model,
        approved.tts.speaker,
        approved.tts.pace,
        approved.tts.sample_rate,
    ) == ("bulbul:v4-flash", "shubh_enhi_ads", 1.0, 24000)
    legacy = voice_for(settings, parse_profile(meta(voice={"speaker": "kavitha"})))
    assert (legacy.tts.model, legacy.tts.speaker) == ("bulbul:v3", "kavitha")


# --- context -------------------------------------------------------------------------


def test_who_the_call_is_with_reaches_the_prompt_within_budget():
    profile = parse_profile(
        meta(context={"name": "  Asha   Rao ", "about": "Brochure request"})
    )
    section = profile_instructions(profile)
    assert "- You are speaking with Asha Rao." in section
    assert "- They enquired about: Brochure request" in section
    long = parse_profile(meta(context={"name": "n" * 500, "about": "a" * 900}))
    assert len(long.caller_name) == 60 and len(long.about) == 160


# --- transcript ------------------------------------------------------------------------


def _item(role, text):
    return llm.ChatMessage(role=role, content=[text])


def test_the_transcript_is_kept_only_when_the_profile_allows_it():
    assert parse_profile(meta()).keep_transcript is False
    assert parse_profile(meta(transcript="yes")).keep_transcript is False
    assert parse_profile(meta(transcript=True)).keep_transcript is True

    off = CallMetrics()
    off.observe(_item("user", "hello"))
    assert "transcript" not in off.ended_fields()

    on = CallMetrics(keep_transcript=True)
    on.observe(_item("assistant", "Hello, who am I speaking to?"))
    on.observe(_item("user", "Call me on 98765 43210 please"))
    on.observe(_item("system", "not part of the conversation"))
    fields = on.ended_fields()
    assert "agent_turns" in fields
    assert fields["transcript"] == [
        {"role": "assistant", "text": "Hello, who am I speaking to?"},
        {"role": "user", "text": "Call me on 98******10 please"},
    ]


def test_a_long_transcript_is_cut_to_fit_one_report():
    metrics = CallMetrics(keep_transcript=True)
    for _ in range(200):
        metrics.observe(_item("user", "x" * 1000))
    kept = metrics.transcript()
    assert sum(len(turn["text"]) for turn in kept) <= TRANSCRIPT_TOTAL_CHARS
    assert all(len(turn["text"]) <= 600 for turn in kept)
    assert len(json.dumps(kept)) < 64_000


# --- inbound lookup --------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


class FakeSession:
    sent: ClassVar[list[dict]] = []
    status = 200
    payload: ClassVar[dict] = {}
    error: Exception | None = None

    def __init__(self, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, data, headers):
        if FakeSession.error:
            raise FakeSession.error
        FakeSession.sent.append({"url": url, "body": data, "headers": headers})
        return FakeResponse(FakeSession.status, FakeSession.payload)


@pytest.fixture(autouse=True)
def fresh_session():
    FakeSession.sent, FakeSession.status, FakeSession.error = [], 200, None
    FakeSession.payload = {}
    yield


def caller():
    return SimpleNamespace(
        attributes={
            "sip.trunkPhoneNumber": "+914012345678",
            "sip.phoneNumber": "+919876543210",
            "sip.callID": "SCL_1",
        },
        kind=None,
        identity="sip_caller",
    )


def inbound_settings(**env):
    return make_settings(CALL_EVENTS_SECRET=SECRET, INBOUND_CONTEXT_URL=URL, **env)


async def test_the_inbound_lookup_is_signed_and_returns_the_employee(caplog):
    FakeSession.payload = {
        "agent_profile": {
            "purpose": "Answer questions.",
            "greeting": "Namaste.",
            "voice": {"speaker": "kavya"},
        }
    }
    profile = await inbound.fetch_profile(
        inbound_settings(), "room-1", caller(), session_factory=FakeSession
    )
    assert profile.greeting == "Namaste." and profile.speaker == "kavya"
    (sent,) = FakeSession.sent
    body = json.loads(sent["body"])
    assert body == {
        "room": "room-1",
        "called": "+914012345678",
        "caller": "+919876543210",
        "sip_call_id": "SCL_1",
    }
    headers = sent["headers"]
    assert headers[SIGNATURE_HEADER] == sign(
        SECRET, headers[TIMESTAMP_HEADER], sent["body"]
    )
    assert "9876543210" not in caplog.text


@pytest.mark.parametrize(
    "status, payload, error",
    [
        (404, {}, None),
        (200, ["not", "a", "dict"], None),
        (200, {"agent_profile": None}, None),
        (200, {}, OSError("network down")),
    ],
)
async def test_inbound_lookup_failures_answer_with_the_fallback(
    status, payload, error, caplog
):
    FakeSession.status, FakeSession.payload, FakeSession.error = status, payload, error
    profile = await inbound.fetch_profile(
        inbound_settings(), "room-1", caller(), session_factory=FakeSession
    )
    assert profile is None
    assert "9876543210" not in caplog.text


async def test_without_the_setting_nothing_is_asked():
    profile = await inbound.fetch_profile(
        make_settings(), "room-1", caller(), session_factory=FakeSession
    )
    assert profile is None and FakeSession.sent == []


@pytest.mark.parametrize(
    "env, message",
    [
        ({"INBOUND_CONTEXT_URL": "ftp://x"}, "must start with http"),
        ({"INBOUND_CONTEXT_URL": URL}, "needs CALL_EVENTS_SECRET"),
    ],
)
def test_bad_inbound_settings_are_rejected(env, message):
    with pytest.raises(ConfigError, match=message):
        make_settings(**env)


# --- LiveKit Cloud recording --------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (False, {"record": False}),
        (True, {"record": {"redaction": True}}),
        ("no", {}),
        (None, {}),
    ],
)
def test_a_profile_decides_whether_livekit_cloud_records_the_session(value, expected):
    from voice_agent.profile import recording_options

    fields = {} if value is None else {"observability": value}
    assert recording_options(parse_profile(meta(**fields))) == expected
    assert recording_options(None) == {}


def test_every_session_start_passes_the_recording_choice():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "voice_agent"
    for name in ("agent.py", "outbound.py", "inbound.py"):
        assert "**recording_options(profile)" in (root / name).read_text(
            encoding="utf-8"
        ), name


@pytest.mark.parametrize("allowed", [False, True])
async def test_the_recording_choice_reaches_a_real_session_start(allowed):
    """The SDK accepts both choices: a text-only session starts and closes."""
    from livekit.agents import AgentSession

    from voice_agent.assistant import TeluguAssistant
    from voice_agent.profile import recording_options

    from .fakes import FakeLLM

    profile = parse_profile(meta(observability=allowed))
    async with AgentSession(llm=FakeLLM()) as session:
        await session.start(
            TeluguAssistant(make_settings(), profile), **recording_options(profile)
        )


def test_a_notice_reaches_the_prompt_within_budget():
    profile = parse_profile(meta(notice="  The office is closed.  Take a message. "))
    assert "- Right now: The office is closed. Take a message." in profile_instructions(
        profile
    )
    assert len(parse_profile(meta(notice="n" * 900)).notice) == 300
    long = parse_profile(
        meta(purpose="అ" * 300, objectives="ఆ" * 400, notice="ఇ" * 300)
    )
    from voice_agent.profile import PROFILE_BUDGET_BYTES

    assert len(profile_instructions(long).encode()) <= PROFILE_BUDGET_BYTES
