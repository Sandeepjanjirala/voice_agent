"""Timing-event trail used to diagnose broken-up speech, and its report."""

import json
import sys
from pathlib import Path

from livekit.agents import AgentSession

from voice_agent.assistant import TeluguAssistant, greet
from voice_agent.config import DEFAULT_GREETING
from voice_agent.observability import attach_session_observers, events_log_path
from voice_agent.prompts import build_instructions

from .fakes import FakeLLM, make_settings

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import call_report


async def test_events_are_written_next_to_the_metrics_without_any_text(
    tmp_path: Path,
) -> None:
    settings = make_settings(VOICE_METRICS_LOG=str(tmp_path / "turns.jsonl"))

    async with AgentSession(llm=FakeLLM("సరే అండి.")) as session:
        attach_session_observers(session, settings, session_id="room-1")
        await session.start(TeluguAssistant(settings))
        await greet(session, settings)
        await session.run(user_input="నా నంబర్ తొమ్మిది ఎనిమిది")

    raw = (tmp_path / "call_events.jsonl").read_text("utf-8")
    rows = [json.loads(line) for line in raw.splitlines()]
    kinds = {row["event"] for row in rows}
    assert {"agent_state", "speech_created", "turn"} <= kinds
    assert all(row["session"] == "room-1" for row in rows)
    # Timings and sizes only: nothing the caller or the agent said.
    assert "తొమ్మిది" not in raw and "సరే" not in raw
    assert all("text" not in row and "transcript" not in row for row in rows)


def test_events_are_off_when_metrics_are_off() -> None:
    assert events_log_path(None) is None
    assert make_settings().metrics_log_path is None


def _state(ts: float, old: str, new: str) -> dict:
    return {"ts": ts, "event": "agent_state", "old": old, "new": new}


def test_report_finds_a_reply_that_stopped_and_resumed() -> None:
    rows = [
        {"ts": 0.0, "event": "speech_created", "source": "generate_reply"},
        _state(0.1, "thinking", "speaking"),
        {"ts": 1.0, "event": "user_state", "old": "listening", "new": "speaking"},
        _state(1.3, "speaking", "listening"),
        {"ts": 3.3, "event": "false_interruption", "resumed": True},
        _state(4.0, "listening", "speaking"),
        _state(6.0, "speaking", "listening"),
        {
            "ts": 6.1,
            "event": "turn",
            "role": "assistant",
            "chars": 43,
            "interrupted": False,
        },
    ]

    summary = call_report.summarise(rows)

    assert summary["pauses_inside_replies"] == [(1.3, 2.7)]
    assert summary["false_interruptions_resumed"] == 1
    assert summary["caller_voice_detected_while_agent_spoke"] == 1
    assert "Pauses inside replies: 1, longest 2.70 s" in call_report.format_report(
        "room-1", summary
    )


def test_report_does_not_count_a_normal_change_of_speaker_as_a_pause() -> None:
    rows = [
        _state(0.0, "thinking", "speaking"),
        _state(2.0, "speaking", "listening"),
        {"ts": 3.0, "event": "turn", "role": "user", "chars": 12, "interrupted": False},
        {"ts": 3.1, "event": "speech_created", "source": "generate_reply"},
        _state(4.0, "thinking", "speaking"),
        _state(6.0, "speaking", "listening"),
    ]

    assert call_report.summarise(rows)["pauses_inside_replies"] == []


def test_prompt_asks_for_spoken_tenglish_and_bans_the_stock_line() -> None:
    prompt = build_instructions("Acme")

    assert "Andhra Pradesh" in prompt
    assert "Never introduce yourself again" in prompt
    assert "why something went wrong" in prompt
    assert "not your greeting" in prompt
    # The style sheet names the stock question only to forbid it.
    assert 'Never say "ఏ విధంగా సహాయం చేయగలను"' in prompt


def test_greeting_is_conversational_and_does_not_use_the_stock_line() -> None:
    assert "సహాయం" not in DEFAULT_GREETING
    assert "help" in DEFAULT_GREETING and "AI" in DEFAULT_GREETING
