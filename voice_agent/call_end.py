"""Lets the agent say goodbye and hang up. Off unless AGENT_CAN_END_CALL=true.

Uses LiveKit's prebuilt EndCallTool, which is still in `beta`: keep every use of
it in this module so an SDK change touches one file.
"""

from __future__ import annotations

from typing import Any

from livekit.agents.beta.tools import EndCallTool

from .config import VoiceSettings

END_CALL_OPTIONS: dict[str, Any] = {
    # Deleting the room is what disconnects a phone caller and stops billing.
    "delete_room": True,
    # The model must not be able to end the call while greeting.
    "ignore_on_enter": True,
    # The goodbye is the tool's reply, so the caller hears it exactly once.
    "end_instructions": (
        "The call is now ending. Do not call any tool again. Say one short, "
        "polite goodbye in the language the caller just used, with no question."
    ),
    "extra_description": (
        "Hanging up on a caller who is not finished is a serious failure. "
        "Never call this on silence, when the caller asks you to wait or hold, "
        "when they thank you but have another question, after a one-word "
        "acknowledgement, or when you are unsure whether they are done."
    ),
}


def end_call_tools(settings: VoiceSettings) -> list[Any]:
    if not settings.can_end_call:
        return []
    return EndCallTool(**END_CALL_OPTIONS).tools
