"""The Telugu assistant agent: instructions, tools and the opening greeting."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator

from livekit import rtc
from livekit.agents import Agent, AgentSession, ModelSettings, stt
from livekit.agents.voice import SpeechHandle

from .acknowledgements import filter_acknowledgements
from .call_end import end_call_tools
from .config import VoiceSettings
from .profile import AgentProfile, business_info_tools, profile_instructions
from .prompts import build_instructions


class TeluguAssistant(Agent):
    def __init__(
        self,
        settings: VoiceSettings,
        profile: AgentProfile | None = None,
        *,
        extra_tools: list | None = None,
    ) -> None:
        business = (profile.business_name if profile else "") or settings.business_name
        super().__init__(
            instructions=build_instructions(
                business,
                can_end_call=settings.can_end_call,
                profile_section=profile_instructions(
                    profile, settings.profile_budget_bytes
                ),
            ),
            tools=[
                *end_call_tools(settings),
                *business_info_tools(profile),
                *(extra_tools or []),
            ],
        )
        self._ignore_acknowledgements = settings.turn.ignore_acknowledgements

    def _speech_in_progress(self) -> bool:
        speech = self.session.current_speech
        return speech is not None and not speech.done() and not speech.interrupted

    async def stt_node(
        self, audio: AsyncIterable[rtc.AudioFrame], model_settings: ModelSettings
    ) -> AsyncIterator[stt.SpeechEvent | str]:
        events = Agent.default.stt_node(self, audio, model_settings)
        if self._ignore_acknowledgements:
            events = filter_acknowledgements(
                events, agent_is_speaking=self._speech_in_progress
            )
        async for event in events:
            yield event


def greet(
    session: AgentSession,
    settings: VoiceSettings,
    *,
    allow_interruptions: bool = True,
    profile: AgentProfile | None = None,
) -> SpeechHandle:
    """Speak the greeting: the profile's if it has one, else the configured one.

    Call this only after the room is connected. Greeting from `Agent.on_enter`
    runs inside `session.start()`, before `ctx.connect()`, and the first audio
    frame then fails with "cannot access local participant before connecting".
    A fixed greeting also skips the LLM, so the caller hears it without waiting
    for a generation. Outbound calls use it too, once the callee has answered.
    """
    greeting = (profile.greeting if profile else "") or settings.greeting
    if profile is not None and profile.recording_notice:
        # Asked before anything is recorded; the answer starts the recording.
        greeting = f"{greeting} {profile.recording_notice}"
    return session.say(greeting, allow_interruptions=allow_interruptions)
