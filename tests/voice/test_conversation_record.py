"""A configured job drives the agent, and the call is recorded as it happened.

- Without an employee the agent stays a cautious receptionist; with one, the
  employee's own configuration decides what it asks and says.
- The conversation is kept with times, interruptions, speech-recognition
  confidence, the tools the agent used and why the call ended, and reaches
  Django in one report that is retried and sent as UTF-8.

No network: the HTTP session is a fake.
"""

import json
from types import SimpleNamespace

import pytest
from livekit.agents import llm

from voice_agent import call_reporting
from voice_agent.call_reporting import CallMetrics, CallReporter
from voice_agent.profile import load_profile_file, parse_profile, profile_instructions
from voice_agent.prompts import (
    PROMPT_BUDGET_BYTES,
    ROLE_WITH_TASK,
    ROLE_WITHOUT_TASK,
    build_instructions,
)

from .test_call_reporting import SECRET, URL, FakeSession


def profile(**fields):
    return parse_profile(json.dumps({"agent_profile": {"purpose": "x", **fields}}))


# --- the prompt follows the configured job ----------------------------------------------


@pytest.mark.parametrize("can_end_call", [False, True])
def test_the_receptionist_rules_apply_only_without_a_task(can_end_call):
    plain = build_instructions("Acme", can_end_call=can_end_call)
    section = profile_instructions(profile(purpose="Collect feedback."))
    task = build_instructions(
        "Acme", can_end_call=can_end_call, profile_section=section
    )
    assert ROLE_WITHOUT_TASK in plain and ROLE_WITH_TASK not in plain
    assert ROLE_WITH_TASK in task and ROLE_WITHOUT_TASK not in task
    # An employee may ask for details; only the plain agent may not.
    assert "ask for no details" not in task
    # The shared part stays within its budget either way; the employee's own
    # section has a budget of its own (AGENT_PROFILE_BUDGET_BYTES).
    assert len(plain.encode()) <= PROMPT_BUDGET_BYTES
    assert len(task.encode()) - len(section.encode()) - 2 <= PROMPT_BUDGET_BYTES


def test_no_kind_of_enquiry_is_built_into_the_base_prompt():
    base = build_instructions("Acme", can_end_call=True, profile_section="- x")
    for word in ("appointment", "admission", "visit", "booking"):
        assert word not in base.lower()


def test_every_configured_part_of_the_job_reaches_the_prompt():
    section = profile_instructions(
        profile(
            purpose="Collect feedback on the recent service.",
            success="The customer rated the service and said one thing to improve.",
            objectives="1. Ask for a rating. 2. Ask what to improve.",
            situations="When they complain about a team member: listen, do not argue.",
            escalation="They report a safety concern.",
            limitations="Never discuss fees.",
            rules="Do not name staff.",
            ending="Thank them once the rating is given.",
        )
    )
    for expected in (
        "- Purpose: Collect feedback on the recent service.",
        "- A successful call: The customer rated the service",
        "- Goals: 1. Ask for a rating.",
        "- Situations: When they complain about a team member",
        "- Hand over to a person when: They report a safety concern.",
        "- Limits: Never discuss fees.",
        "- Business rules: Do not name staff.",
        "- Ending: Thank them once the rating is given.",
        "you cannot transfer the call",
    ):
        assert expected in section


def test_an_employee_never_promises_what_only_staff_can_do():
    kept = profile_instructions(profile(transcript=True))
    unkept = profile_instructions(profile())
    for section in (kept, unkept):
        assert "never say you will update, book, send, change or arrange" in section
    # "Noted" is true only when the organisation keeps what is said.
    assert "never say you noted anything" in unkept
    assert "never say you noted anything" not in kept


def test_a_profile_with_only_new_fields_is_a_profile_and_older_ones_still_work():
    assert parse_profile(
        json.dumps({"agent_profile": {"situations": "When asked: answer."}})
    )
    old = profile_instructions(profile(purpose="Follow up.", objectives="Ask."))
    assert "Situations" not in old and "Hand over" not in old
    long = parse_profile(
        json.dumps({"agent_profile": {"purpose": "x", "rules": "r" * 5000}})
    )
    assert len(long.rules) == 1200


def test_a_console_profile_file_is_read_and_a_bad_one_ignored(tmp_path):
    good = tmp_path / "feedback.json"
    good.write_text(
        json.dumps({"agent_profile": {"purpose": "అభిప్రాయం తీసుకోండి"}}),
        encoding="utf-8",
    )
    assert load_profile_file(str(good)).purpose == "అభిప్రాయం తీసుకోండి"
    assert load_profile_file(str(tmp_path / "missing.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert load_profile_file(str(bad)) is None


# --- the conversation as it happened ---------------------------------------------------


def message(role, text, at, **extra):
    return llm.ChatMessage(role=role, content=[text], created_at=at, **extra)


def tools(*pairs, at=0.0):
    calls = [
        llm.FunctionCall(call_id=f"c{i}", name=name, arguments="{}", created_at=at)
        for i, (name, _) in enumerate(pairs)
    ]
    outputs = [
        llm.FunctionCallOutput(
            call_id=f"c{i}", name=name, output="secret result", is_error=not ok
        )
        for i, (name, ok) in enumerate(pairs)
    ]
    return SimpleNamespace(function_calls=calls, function_call_outputs=outputs)


def test_turns_keep_time_interruption_confidence_and_tools_but_no_tool_data():
    metrics = CallMetrics(keep_transcript=True, started_at=1000.0)
    metrics.observe(message("assistant", "Hello, Meera here.", 1000.4))
    metrics.observe(
        message("user", "My number is 98765 43210", 1003.25, transcript_confidence=0.82)
    )
    metrics.observe(message("assistant", "Thank you, I will", 1005.0, interrupted=True))
    metrics.observe_tools(
        tools(("get_business_info", True), ("end_call", False), at=1007)
    )
    kept = metrics.transcript()
    assert kept == [
        {"role": "assistant", "text": "Hello, Meera here.", "t": 0.4},
        {
            "role": "user",
            "text": "My number is 98765 43210",
            "t": 3.2,
            "confidence": 0.82,
        },
        {
            "role": "assistant",
            "text": "Thank you, I will",
            "t": 5.0,
            "interrupted": True,
        },
        {"role": "tool", "name": "get_business_info", "ok": True, "t": 7.0},
        {"role": "tool", "name": "end_call", "ok": False, "t": 7.0},
    ]
    assert "secret result" not in json.dumps(kept)


def test_without_permission_no_conversation_is_kept_but_the_end_reason_is():
    metrics = CallMetrics(keep_transcript=False)
    metrics.observe(message("user", "hello", 1.0))
    metrics.observe_tools(tools(("end_call", True)))
    metrics.finish(talk_seconds=20)
    fields = metrics.ended_fields()
    assert "transcript" not in fields
    assert fields["end_reason"] == "agent_ended"


@pytest.mark.parametrize(
    ("hint", "talk", "limit", "used", "expected"),
    [
        ("caller_hung_up", 40, 300, (), "caller_hung_up"),
        ("caller_hung_up", 299, 300, (), "time_limit"),
        ("caller_hung_up", 40, 300, ("end_call",), "agent_ended"),
        ("error", 12, 300, (), "error"),
        ("", 12, 300, (), "system"),
    ],
)
def test_why_the_call_ended(hint, talk, limit, used, expected):
    metrics = CallMetrics()
    if hint:
        metrics.note_end(hint)
    metrics.tools_used.update(used)
    metrics.finish(talk_seconds=talk, limit=limit)
    assert metrics.ended_fields()["end_reason"] == expected


def test_the_session_close_event_gives_the_reason():
    from livekit.agents.voice.events import CloseReason

    metrics = CallMetrics()
    metrics.on_close(
        SimpleNamespace(reason=CloseReason.PARTICIPANT_DISCONNECTED, error=None)
    )
    metrics.finish(talk_seconds=5)
    assert metrics.end_reason == "caller_hung_up"
    failed = CallMetrics()
    failed.on_close(SimpleNamespace(reason=CloseReason.ERROR, error=RuntimeError()))
    failed.note_end("caller_hung_up")  # the first thing seen wins
    failed.finish(talk_seconds=5)
    assert failed.end_reason == "error"


# --- delivery --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_session():
    FakeSession.sent, FakeSession.status, FakeSession.error = [], 204, None


@pytest.fixture
def no_wait(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(call_reporting.asyncio, "sleep", instant)


def reporter():
    return CallReporter(URL, SECRET, "outcall-test", session_factory=FakeSession)


async def test_the_ended_report_is_retried_when_django_is_unreachable(no_wait):
    attempts = []

    class Flaky(FakeSession):
        def post(self, url, data, headers):
            attempts.append(data)
            if len(attempts) < 3:
                raise ConnectionError("django restarting")
            return super().post(url, data, headers)

    sender = CallReporter(URL, SECRET, "outcall-test", session_factory=Flaky)
    assert await sender.send("ended", outcome="completed") is True
    assert len(attempts) == 3


async def test_a_refused_report_is_not_retried_and_other_events_are_sent_once(no_wait):
    FakeSession.status = 413
    assert await reporter().send("ended", outcome="completed") is False
    assert len(FakeSession.sent) == 1
    FakeSession.sent, FakeSession.status = [], 503
    assert await reporter().send("answered") is False
    assert len(FakeSession.sent) == 1
    FakeSession.sent = []
    assert await reporter().send("ended", outcome="completed") is False
    assert len(FakeSession.sent) == call_reporting.ENDED_ATTEMPTS


async def test_the_report_is_utf8_so_a_long_telugu_call_fits(no_wait):
    metrics = CallMetrics(keep_transcript=True)
    for second in range(400):
        metrics.observe(message("user", "తెలుగు " * 80, float(second)))
    metrics.finish(talk_seconds=600)
    assert await reporter().send("ended", outcome="completed", **metrics.ended_fields())
    body = FakeSession.sent[0]["body"]
    assert len(body) < 65_536, "Django accepts up to 64 kB"
    assert "తెలుగు".encode() in body and b"\\u0c24" not in body
    assert json.loads(body)["end_reason"] == "system"
