"""What this agent is for: purpose, goals, limits and business facts.

The profile is edited in the Django application and reaches the worker as job
metadata. The worker never trusts its size: every field is clamped here, the
prompt section has its own byte budget, and business facts stay out of the
prompt behind a tool, so the per-turn prompt stays small.

A profile may also choose the voice for its call (speaker, pace, language), say
who the call is with, and allow the transcript to be reported to Django. Each
of these is optional; a profile without them behaves as before.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from typing import Any

from livekit.agents import function_tool
from livekit.plugins.sarvam.models import MODEL_SPEAKER_COMPATIBILITY

from .config import VoiceSettings

logger = logging.getLogger("voice_agent")

PROFILE_KEY = "agent_profile"
# Budget for the per-call section added to the system prompt.
PROFILE_BUDGET_BYTES = 1024

_LIMITS = {
    "business_name": 80,
    "agent_name": 40,
    "purpose": 300,
    "greeting": 250,
    "objectives": 400,
    "limitations": 300,
    "ending": 200,
    "business_info": 4000,
}

# Language codes a profile may choose, for both speech recognition and voice.
LANGUAGES = ("te-IN", "en-IN", "hi-IN")
PACE_RANGE = (0.5, 2.0)
_CONTEXT_LIMITS = {"name": 60, "about": 160}
# A short situational note from Django, e.g. that the office is closed now.
NOTICE_LIMIT = 300

NO_INFO_REPLY = (
    "No business information is available. Say our office team will call back "
    "with the details. Do not guess."
)


@dataclass(frozen=True)
class AgentProfile:
    business_name: str = ""
    agent_name: str = ""
    purpose: str = ""
    greeting: str = ""
    objectives: str = ""
    limitations: str = ""
    ending: str = ""
    business_info: str = ""
    # The voice for this call; empty or None keeps the worker's own setting.
    speaker: str = ""
    pace: float | None = None
    language: str = ""
    # Who the call is with and what it is about, when Django knows.
    caller_name: str = ""
    about: str = ""
    notice: str = ""
    # Whether the transcript may be sent to Django when the call ends.
    keep_transcript: bool = False
    # Whether LiveKit Cloud may keep this session's audio, transcript, traces
    # and logs (Agent observability). None follows the project setting.
    cloud_recording: bool | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.purpose,
                self.objectives,
                self.limitations,
                self.ending,
                self.greeting,
            )
        ) and not (self.business_name or self.business_info)


def parse_profile(metadata: str | None) -> AgentProfile | None:
    """The agent profile in job metadata, or None when there is none."""
    try:
        data = json.loads(metadata) if metadata else None
    except ValueError:
        return None
    raw = data.get(PROFILE_KEY) if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return None
    fields = {
        name: " ".join(str(raw.get(name) or "").split())[:limit]
        if name != "business_info"
        else str(raw.get(name) or "").strip()[:limit]
        for name, limit in _LIMITS.items()
    }
    voice = raw.get("voice") if isinstance(raw.get("voice"), dict) else {}
    context = raw.get("context") if isinstance(raw.get("context"), dict) else {}
    try:
        pace = float(voice["pace"]) if voice.get("pace") is not None else None
    except (TypeError, ValueError):
        pace = None
    language = str(voice.get("language") or "")
    profile = AgentProfile(
        **fields,
        speaker=str(voice.get("speaker") or "").strip().lower()[:40],
        pace=min(max(pace, PACE_RANGE[0]), PACE_RANGE[1]) if pace else None,
        language=language if language in LANGUAGES else "",
        caller_name=" ".join(str(context.get("name") or "").split())[
            : _CONTEXT_LIMITS["name"]
        ],
        about=" ".join(str(context.get("about") or "").split())[
            : _CONTEXT_LIMITS["about"]
        ],
        notice=" ".join(str(raw.get("notice") or "").split())[:NOTICE_LIMIT],
        keep_transcript=raw.get("transcript") is True,
        cloud_recording=raw["observability"]
        if isinstance(raw.get("observability"), bool)
        else None,
    )
    return None if profile.is_empty else profile


def recording_options(profile: AgentProfile | None) -> dict[str, Any]:
    """Keyword arguments for AgentSession.start() that say whether LiveKit
    Cloud may record the session. Empty follows the project setting.

    An organisation that allows recording still gets LiveKit's PII redaction
    on transcripts, audio and telemetry, whatever the project setting."""
    if profile is None or profile.cloud_recording is None:
        return {}
    return {"record": {"redaction": True} if profile.cloud_recording else False}


def voice_for(settings: VoiceSettings, profile: AgentProfile | None) -> VoiceSettings:
    """The worker's settings with this profile's voice choices applied.

    A speaker that the configured TTS model does not have is ignored (and
    logged), so a bad profile can never stop a call from being answered.
    """
    if profile is None:
        return settings
    tts, stt = settings.tts, settings.stt
    if profile.speaker:
        voices = MODEL_SPEAKER_COMPATIBILITY.get(tts.model, {}).get("all", ())
        if profile.speaker in voices:
            tts = replace(tts, speaker=profile.speaker)
        else:
            logger.warning(
                "profile voice is not available; using the configured voice",
                extra={"speaker": profile.speaker, "tts_model": tts.model},
            )
    if profile.pace:
        tts = replace(tts, pace=profile.pace)
    if profile.language:
        tts = replace(tts, language=profile.language)
        stt = replace(stt, language=profile.language)
    return replace(settings, tts=tts, stt=stt)


def profile_instructions(profile: AgentProfile | None) -> str:
    """The "This call" prompt section, never larger than PROFILE_BUDGET_BYTES."""
    if profile is None:
        return ""
    lines = ["# This call"]
    if profile.agent_name:
        lines.append(f"- Your name is {profile.agent_name}.")
    if profile.caller_name:
        lines.append(f"- You are speaking with {profile.caller_name}.")
    if profile.about:
        lines.append(f"- They enquired about: {profile.about}")
    if profile.notice:
        lines.append(f"- Right now: {profile.notice}")
    for label, value in (
        ("Purpose", profile.purpose),
        ("Goals", profile.objectives),
        ("Limits", profile.limitations),
        ("Ending", profile.ending),
    ):
        if value:
            lines.append(f"- {label}: {value}")
    lines.append(
        "- Work towards the purpose; never ask what help the caller needs if you "
        "already know why you are talking."
    )
    if profile.business_info:
        lines.append(
            "- For any fact about the business (timings, address, services, fees), "
            "call get_business_info and use only what it returns."
        )
    # Drop whole lines from the end rather than cut a sentence in half.
    while (
        len("\n".join(lines).encode("utf-8")) > PROFILE_BUDGET_BYTES and len(lines) > 2
    ):
        lines.pop(-2 if profile.business_info and len(lines) > 3 else -1)
    return "\n".join(lines)


def business_info_tools(profile: AgentProfile | None) -> list[Any]:
    """A lookup tool for business facts, so they are not re-sent on every turn."""
    if profile is None or not profile.business_info:
        return []
    info = profile.business_info

    @function_tool
    async def get_business_info() -> str:
        """Returns the facts the business has provided: timings, address,
        services, fees and similar. Call this before stating any such fact.
        If the answer is not in what it returns, say our office team will call
        back; never guess."""
        return info or NO_INFO_REPLY

    return [get_business_info]
