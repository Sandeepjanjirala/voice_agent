"""Runs the real AgentSession in text mode against a fake LLM.

These check our wiring (greeting, prompt, error fallback). They say nothing
about speech quality, real model replies or latency: those need live providers
and a microphone, as described in docs/history/PHASE1_VERIFICATION.md.
"""

import asyncio
import contextlib
import json
import logging
from pathlib import Path

import pytest
from livekit.agents import AgentSession, APIConnectionError

from voice_agent.assistant import TeluguAssistant, greet
from voice_agent.observability import attach_session_observers

from .fakes import FakeLLM, make_settings


def _assistant_texts(result) -> list[str]:
    return [
        e.item.text_content
        for e in result.events
        if e.type == "message" and e.item.role == "assistant"
    ]


async def test_agent_greets_first_with_the_configured_greeting() -> None:
    settings = make_settings(AGENT_GREETING="నమస్తే! చెప్పండి.")
    fake = FakeLLM()

    async with AgentSession(llm=fake) as session:
        started = await session.start(TeluguAssistant(settings), capture_run=True)
        # Nothing may be spoken while starting: the room is not connected yet.
        assert _assistant_texts(started) == []

        await greet(session, settings)
        spoken = [
            i.text_content
            for i in session.history.items
            if i.type == "message" and i.role == "assistant"
        ]

    assert spoken == ["నమస్తే! చెప్పండి."]
    # The greeting is fixed text: it must not cost an LLM round trip.
    assert fake.requests == []


async def test_user_turn_reaches_the_llm_with_our_instructions() -> None:
    settings = make_settings(BUSINESS_NAME="Sunrise Clinic")
    fake = FakeLLM("సరేనండి, చెప్పండి.")

    async with AgentSession(llm=fake) as session:
        await session.start(TeluguAssistant(settings))
        result = await session.run(user_input="నాకు appointment కావాలి")

    assert _assistant_texts(result) == ["సరేనండి, చెప్పండి."]
    messages = [i for i in fake.requests[-1].items if i.type == "message"]
    assert messages[0].role == "system"
    assert "Sunrise Clinic" in messages[0].text_content
    assert messages[-1].role == "user"
    assert messages[-1].text_content == "నాకు appointment కావాలి"


async def test_llm_outage_speaks_fallback_and_session_recovers() -> None:
    settings = make_settings()
    fake = FakeLLM("మళ్ళీ చెప్పండి.")

    async with AgentSession(llm=fake) as session:
        fallback = attach_session_observers(session, settings, session_id="test")
        await session.start(TeluguAssistant(settings))

        fake.fail = True
        failed = await asyncio.wait_for(session.run(user_input="హలో"), timeout=30)
        assert _assistant_texts(failed) == []
        assert fallback.spoken_count == 1

        # One failed turn must not end the conversation.
        fake.fail = False
        recovered = await asyncio.wait_for(session.run(user_input="హలో"), timeout=30)
        assert _assistant_texts(recovered) == ["మళ్ళీ చెప్పండి."]


async def test_repeated_outages_do_not_repeat_the_apology_in_a_loop() -> None:
    settings = make_settings()
    fake = FakeLLM(fail=True)

    async with AgentSession(llm=fake) as session:
        fallback = attach_session_observers(session, settings, session_id="test")
        await session.start(TeluguAssistant(settings))
        for _ in range(2):
            # A failed turn with nothing spoken surfaces the provider error to
            # the test harness; in a live call it is only logged.
            with contextlib.suppress(APIConnectionError):
                await asyncio.wait_for(session.run(user_input="హలో"), timeout=30)

    assert fallback.spoken_count == 1


async def test_turns_are_written_to_the_metrics_log(tmp_path: Path) -> None:
    log_path = tmp_path / "turns.jsonl"
    settings = make_settings(VOICE_METRICS_LOG=str(log_path))

    async with AgentSession(llm=FakeLLM("సరే.")) as session:
        attach_session_observers(session, settings, session_id="room-1")
        await session.start(TeluguAssistant(settings))
        await greet(session, settings)
        await session.run(user_input="హలో")

    rows = [json.loads(line) for line in log_path.read_text("utf-8").splitlines()]
    assert [r["role"] for r in rows] == ["assistant", "user", "assistant"]
    assert all(r["session"] == "room-1" for r in rows)
    # Latency rows carry sizes and timings only, never what was said.
    assert all("text" not in r and "transcript" not in r for r in rows)


async def test_transcript_logging_can_be_switched_off(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = make_settings(VOICE_LOG_TRANSCRIPTS="false")

    with caplog.at_level(logging.INFO, logger="voice_agent"):
        async with AgentSession(llm=FakeLLM("రహస్యం.")) as session:
            attach_session_observers(session, settings, session_id="test")
            await session.start(TeluguAssistant(settings))
            await session.run(user_input="నా నంబర్ తొమ్మిది ఎనిమిది")

    turns = [r for r in caplog.records if r.message == "turn completed"]
    assert turns
    assert all(not hasattr(r, "text") for r in turns)


async def test_phone_greeting_cannot_be_interrupted() -> None:
    settings = make_settings()

    async with AgentSession(llm=FakeLLM()) as session:
        await session.start(TeluguAssistant(settings))
        normal = greet(session, settings)
        await normal
        phone = greet(session, settings, allow_interruptions=False)
        await phone

    assert normal.allow_interruptions is True
    assert phone.allow_interruptions is False


def test_outbound_greeting_is_the_uninterruptible_one() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "voice_agent" / "outbound.py"
    ).read_text(encoding="utf-8")

    assert (
        "greet(session, settings, allow_interruptions=False, profile=profile)" in source
    )
