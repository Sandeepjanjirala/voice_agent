"""The Telugu assistant agent: instructions plus the opening greeting."""

from __future__ import annotations

from livekit.agents import Agent, AgentSession
from livekit.agents.voice import SpeechHandle

from .call_end import end_call_tools
from .config import VoiceSettings
from .prompts import build_instructions


class TeluguAssistant(Agent):
    def __init__(self, settings: VoiceSettings) -> None:
        super().__init__(
            instructions=build_instructions(
                settings.business_name, can_end_call=settings.can_end_call
            ),
            tools=end_call_tools(settings),
        )


def greet(session: AgentSession, settings: VoiceSettings) -> SpeechHandle:
    """Speak the configured greeting.

    Call this only after the room is connected. Greeting from `Agent.on_enter`
    runs inside `session.start()`, before `ctx.connect()`, and the first audio
    frame then fails with "cannot access local participant before connecting".
    A fixed greeting also skips the LLM, so the caller hears it without waiting
    for a generation. Outbound calls use it too, once the callee has answered.
    """
    return session.say(settings.greeting)
