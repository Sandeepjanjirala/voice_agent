"""Agent profile in the worker, and the acknowledgement filter.

The filter tests drive the real filter with scripted transcript events and a
controllable "agent is speaking" flag. They prove the decision logic; how it
feels on a phone line needs a real call.
"""

import asyncio
import json

import pytest
from livekit.agents import AgentSession, stt

from voice_agent.acknowledgements import filter_acknowledgements, is_acknowledgement
from voice_agent.assistant import TeluguAssistant, greet
from voice_agent.profile import (
    PROFILE_BUDGET_BYTES,
    AgentProfile,
    business_info_tools,
    parse_profile,
    profile_instructions,
)
from voice_agent.prompts import PROMPT_BUDGET_BYTES, build_instructions

from .fakes import FakeLLM, make_settings

FINAL = stt.SpeechEventType.FINAL_TRANSCRIPT
INTERIM = stt.SpeechEventType.INTERIM_TRANSCRIPT


def meta(**profile) -> str:
    return json.dumps({"agent_profile": profile})


# --- profile -------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw", [None, "", "not json", "[]", "{}", '{"agent_profile": 5}', meta()]
)
def test_jobs_without_a_profile(raw) -> None:
    assert parse_profile(raw) is None


def test_profile_fields_are_clamped() -> None:
    profile = parse_profile(meta(purpose="p" * 5000, business_info="i" * 9000))

    assert len(profile.purpose) == 300
    assert len(profile.business_info) == 4000


def test_profile_section_states_purpose_and_stays_in_budget() -> None:
    profile = parse_profile(
        meta(
            purpose="అ" * 300,
            objectives="ఆ" * 400,
            limitations="ఇ" * 300,
            ending="ఈ" * 200,
            business_info="Open 9 to 5.",
        )
    )

    section = profile_instructions(profile)

    assert section.startswith("# This call")
    assert "Purpose:" in section
    assert len(section.encode("utf-8")) <= PROFILE_BUDGET_BYTES


def test_prompt_without_a_profile_is_unchanged_and_within_budget() -> None:
    plain = build_instructions("Acme")

    assert build_instructions("Acme", profile_section="") == plain
    assert "# This call" not in plain
    assert len(plain.encode("utf-8")) <= PROMPT_BUDGET_BYTES


def test_assistant_uses_profile_business_name_purpose_and_tool() -> None:
    profile = parse_profile(
        meta(
            business_name="Sunrise Clinic",
            purpose="Confirm the visit.",
            business_info="Open 9 to 5.",
        )
    )

    agent = TeluguAssistant(make_settings(), profile)

    assert "Sunrise Clinic" in agent.instructions
    assert "Purpose: Confirm the visit." in agent.instructions
    assert [tool.info.name for tool in agent.tools] == ["get_business_info"]
    # Facts stay behind the tool, out of the per-turn prompt.
    assert "Open 9 to 5." not in agent.instructions


def test_no_business_information_means_no_lookup_tool() -> None:
    assert business_info_tools(AgentProfile(purpose="x")) == []
    assert TeluguAssistant(make_settings()).tools == []


async def test_business_info_tool_returns_only_what_was_written() -> None:
    (tool,) = business_info_tools(AgentProfile(business_info="Open 9 to 5."))

    assert await tool() == "Open 9 to 5."


async def test_profile_greeting_replaces_the_default() -> None:
    settings = make_settings()
    profile = AgentProfile(greeting="హలో అండి! Appointment గురించి call చేశాను.")

    async with AgentSession(llm=FakeLLM()) as session:
        await session.start(TeluguAssistant(settings, profile))
        await greet(session, settings, profile=profile)
        spoken = [i.text_content for i in session.history.items if i.type == "message"]

    assert spoken == [profile.greeting]


def test_prompt_tells_the_agent_not_to_re_ask_after_an_acknowledgement() -> None:
    assert "do not ask what help they need again" in build_instructions("Acme")


# --- acknowledgements ------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["ఆ.", "హా", "ఊ...", "ఆ ఆ", "Okay.", "hmm", "సరే", "ok ok"]
)
def test_acknowledgements_are_recognised(text) -> None:
    assert is_acknowledgement(text)


@pytest.mark.parametrize(
    "text",
    [
        "ఆగండి",
        "Stop",
        "stop.",
        "bye",
        "Bye!",
        "వద్దు",
        "చాలు",
        "help",
        "cancel",
        "phone cut చేయండి",
        "ఒక్క నిమిషం",
        "ఆ, ఒక doubt",
        "okay thank you bye",
        "ఎప్పుడు?",
        "అవును",
        "కాదు",
        "no",
        "yes",
        "",
    ],
)
def test_commands_questions_and_answers_are_never_acknowledgements(text) -> None:
    assert not is_acknowledgement(text)


def event(kind, text: str) -> stt.SpeechEvent:
    return stt.SpeechEvent(
        type=kind, alternatives=[stt.SpeechData(language="te-IN", text=text)]
    )


class Line:
    """A scripted transcript stream and a switch for "the agent is speaking"."""

    def __init__(self, speaking: bool) -> None:
        self.speaking = speaking
        self.queue: asyncio.Queue = asyncio.Queue()

    async def events(self):
        while (item := await self.queue.get()) is not None:
            yield item

    def say(self, kind, text: str) -> None:
        self.queue.put_nowait(event(kind, text))

    def close(self) -> None:
        self.queue.put_nowait(None)


async def collect(line: Line, **kwargs) -> list[str]:
    out = []
    async for item in filter_acknowledgements(
        line.events(), agent_is_speaking=lambda: line.speaking, **kwargs
    ):
        out.append(item.alternatives[0].text)
    return out


async def test_acknowledgement_while_the_agent_speaks_is_held_back() -> None:
    line = Line(speaking=True)
    line.say(INTERIM, "ఆ")
    line.say(FINAL, "ఆ.")
    line.close()

    assert await collect(line) == []


@pytest.mark.parametrize("word", ["ఆగండి", "Stop", "bye", "help", "వద్దు"])
async def test_single_word_commands_always_pass_while_the_agent_speaks(word) -> None:
    line = Line(speaking=True)
    line.say(FINAL, word)
    line.close()

    assert await collect(line) == [word]


async def test_questions_and_overlapping_speech_pass_untouched() -> None:
    line = Line(speaking=True)
    line.say(INTERIM, "ఒక్క నిమిషం")
    line.say(FINAL, "ఒక్క నిమిషం, fee ఎంత?")
    line.close()

    assert await collect(line) == ["ఒక్క నిమిషం", "ఒక్క నిమిషం, fee ఎంత?"]


async def test_acknowledgement_when_the_agent_is_silent_is_a_normal_reply() -> None:
    line = Line(speaking=False)
    line.say(FINAL, "ఆ.")
    line.close()

    assert await collect(line) == ["ఆ."]


async def test_acknowledgement_just_before_the_agent_finishes_is_delivered_as_an_answer() -> (
    None
):
    line = Line(speaking=True)
    line.say(FINAL, "ఆ.")

    async def agent_finishes() -> None:
        await asyncio.sleep(0.05)
        line.speaking = False
        await asyncio.sleep(0.1)
        line.close()

    task = asyncio.create_task(agent_finishes())
    result = await collect(line, poll_seconds=0.01)
    await task

    assert result == ["ఆ."]


async def test_acknowledgement_in_the_middle_of_a_long_reply_is_dropped() -> None:
    line = Line(speaking=True)
    line.say(FINAL, "ఊ")

    async def later() -> None:
        await asyncio.sleep(0.12)  # longer than the hold window below
        line.speaking = False
        await asyncio.sleep(0.05)
        line.close()

    task = asyncio.create_task(later())
    result = await collect(line, hold_seconds=0.05, poll_seconds=0.01)
    await task

    assert result == []


async def test_real_words_after_an_acknowledgement_replace_it() -> None:
    line = Line(speaking=True)
    line.say(FINAL, "ఆ.")
    line.say(FINAL, "ఆగండి")
    line.close()

    assert await collect(line) == ["ఆగండి"]


async def test_noise_without_a_transcript_is_left_to_livekit() -> None:
    line = Line(speaking=True)
    line.queue.put_nowait(stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH))
    line.queue.put_nowait(stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH))
    line.close()
    passed = []
    async for item in filter_acknowledgements(
        line.events(), agent_is_speaking=lambda: True
    ):
        passed.append(item.type)

    assert passed == [
        stt.SpeechEventType.START_OF_SPEECH,
        stt.SpeechEventType.END_OF_SPEECH,
    ]


def test_filter_is_off_by_default_and_switchable() -> None:
    assert make_settings().turn.ignore_acknowledgements is False
    assert (
        make_settings(INTERRUPTION_IGNORE_ACKS="true").turn.ignore_acknowledgements
        is True
    )
