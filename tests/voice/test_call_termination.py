"""Wiring of the optional end-call tool (voice_agent/call_end.py).

Written before the code, from docs/CALL_TERMINATION_DESIGN.md; the
implementation was made to pass them. Whether the model ends calls at the
right moments is covered by scenarios/call_termination.yaml.
"""

from voice_agent import call_end
from voice_agent.assistant import TeluguAssistant
from voice_agent.prompts import PROMPT_BUDGET_BYTES, build_instructions

from .fakes import make_settings


def _tool_names(tools) -> list[str]:
    return [tool.info.name for tool in tools]


def test_ending_calls_is_off_unless_enabled() -> None:
    settings = make_settings()

    assert settings.can_end_call is False
    assert call_end.end_call_tools(settings) == []
    assert TeluguAssistant(settings).tools == []


def test_disabled_prompt_is_byte_identical_to_today() -> None:
    assert build_instructions("Sunrise Clinic", can_end_call=False) == (
        build_instructions("Sunrise Clinic")
    )


def test_enabled_agent_has_exactly_one_end_call_tool() -> None:
    settings = make_settings(AGENT_CAN_END_CALL="true")

    assert _tool_names(call_end.end_call_tools(settings)) == ["end_call"]
    assert _tool_names(TeluguAssistant(settings).tools) == ["end_call"]


def test_tool_description_forbids_hanging_up_on_silence_hold_or_doubt() -> None:
    settings = make_settings(AGENT_CAN_END_CALL="true")

    description = call_end.end_call_tools(settings)[0].info.description.lower()

    for phrase in ("silence", "hold", "another question", "unsure"):
        assert phrase in description


def test_tool_cannot_fire_during_the_greeting() -> None:
    assert call_end.END_CALL_OPTIONS["ignore_on_enter"] is True


def test_hangup_deletes_the_room_so_the_phone_line_is_released() -> None:
    # Deleting the room is what disconnects a SIP caller; leaving it open would
    # keep the line, and the bill, running after the goodbye.
    assert call_end.END_CALL_OPTIONS["delete_room"] is True


def test_goodbye_is_generated_once_by_the_tool_reply() -> None:
    enabled = build_instructions("Sunrise Clinic", can_end_call=True)

    # The standing "reply with one short goodbye" rule is replaced, not kept
    # next to the tool, or the caller would hear two goodbyes.
    assert "end_call" in enabled
    assert "reply with one short goodbye and no new question" not in enabled


def test_enabled_prompt_stays_within_the_budget() -> None:
    enabled = build_instructions("Sunrise Clinic", can_end_call=True)

    assert len(enabled.encode("utf-8")) <= PROMPT_BUDGET_BYTES
