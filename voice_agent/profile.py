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
# A phone call's profile by reference: Django keeps the call's immutable
# snapshot, and the worker fetches it (call_profile.py). Job metadata then
# carries no instructions or personal details.
PROFILE_REF_KEY = "agent_profile_ref"
# Default budget for the per-call section added to the system prompt; the
# worker's own setting is AGENT_PROFILE_BUDGET_BYTES.
PROFILE_BUDGET_BYTES = 8192

# Characters per field; kept in step with apps/workforce/publishing.py.
_LIMITS = {
    "business_name": 160,
    "agent_name": 60,
    "purpose": 1200,
    "greeting": 400,
    "objectives": 9000,
    "limitations": 1800,
    "ending": 600,
    "business_info": 8000,
    # What a good call achieves, special situations and when to hand over, as
    # the business configured them; and the business's own rules (Knowledge).
    "success": 600,
    "situations": 4000,
    "escalation": 600,
    "rules": 1200,
}

# Language codes a profile may choose, for both speech recognition and voice.
LANGUAGES = ("te-IN", "en-IN", "hi-IN")
PACE_RANGE = (0.5, 2.0)
_CONTEXT_LIMITS = {"name": 60, "about": 160}
# A short situational note from Django, e.g. that the office is closed now.
NOTICE_LIMIT = 300
# The recording question Django allows for this call (recording.py).
RECORDING_NOTICE_LIMIT = 300

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
    success: str = ""
    situations: str = ""
    escalation: str = ""
    rules: str = ""
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
    # Set only when Django allows this call to be recorded: the question said
    # after the opening line. Recording starts only after a clear yes.
    recording_notice: str = ""
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
                self.success,
                self.situations,
                self.escalation,
                self.rules,
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
        recording_notice=_recording_notice(raw.get("recording")),
        cloud_recording=raw["observability"]
        if isinstance(raw.get("observability"), bool)
        else None,
    )
    return None if profile.is_empty else profile


def _recording_notice(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return " ".join(str(value.get("notice") or "").split())[:RECORDING_NOTICE_LIMIT]


def load_profile_file(path: str) -> AgentProfile | None:
    """A profile from a local JSON file, for console testing only
    (CONSOLE_PROFILE_FILE). A missing or unreadable file means no profile."""
    try:
        with open(path, encoding="utf-8") as file:
            text = file.read(256_000)
    except OSError as error:
        logger.warning(
            "console profile file not read", extra={"error_type": type(error).__name__}
        )
        return None
    profile = parse_profile(text)
    if profile is None:
        logger.warning("console profile file holds no usable agent_profile")
    return profile


def parse_profile_ref(metadata: str | None) -> dict[str, str] | None:
    """The signed reference to a call's profile in job metadata, or None."""
    try:
        data = json.loads(metadata) if metadata else None
    except ValueError:
        return None
    ref = data.get(PROFILE_REF_KEY) if isinstance(data, dict) else None
    if not isinstance(ref, dict):
        return None
    fields = {
        name: str(ref.get(name) or "")[:128] for name in ("call", "expires", "token")
    }
    return fields if all(fields.values()) else None


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
        # Saved v3 employees retain their voice when the deployment moves to v4.
        # Persona IDs are distinct from the classic v3 speaker names.
        model = tts.model
        if profile.speaker == "shubh_enhi_ads":
            model = "bulbul:v4-flash"
        elif (
            model == "bulbul:v4-flash"
            and profile.speaker in MODEL_SPEAKER_COMPATIBILITY["bulbul:v3"]["all"]
        ):
            model = "bulbul:v3"
        voices = MODEL_SPEAKER_COMPATIBILITY.get(model, {}).get("all", ())
        if profile.speaker in voices:
            tts = replace(
                tts,
                model=model,
                speaker=profile.speaker,
                sample_rate=24000 if model == "bulbul:v4-flash" else tts.sample_rate,
            )
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


def profile_instructions(
    profile: AgentProfile | None, budget: int = PROFILE_BUDGET_BYTES
) -> str:
    """The "This call" prompt section, never larger than `budget` bytes."""
    if profile is None:
        return ""
    lines = ["# This call"]
    if profile.agent_name:
        lines.append(f"- Your name is {profile.agent_name}.")
    if profile.caller_name:
        lines.append(f"- You are speaking with {profile.caller_name}.")
    if profile.about:
        lines.append(f"- This call is about: {profile.about}")
    if profile.notice:
        lines.append(f"- Right now: {profile.notice}")
    # Everything below is the business's own configuration for this job.
    for label, value in (
        ("Purpose", profile.purpose),
        ("A successful call", profile.success),
        ("Goals", profile.objectives),
        ("Situations", profile.situations),
        ("Hand over to a person when", profile.escalation),
        ("Limits", profile.limitations),
        ("Business rules", profile.rules),
        ("Ending", profile.ending),
    ):
        if value:
            lines.append(f"- {label}: {value}")
    if profile.escalation:
        lines.append(
            "- To hand over, say a team member will call them back; you cannot "
            "transfer the call."
        )
    # The employee only talks; staff and the system act on the call afterwards.
    lines.append(
        "- You only talk and collect: never say you will update, book, send, "
        "change or arrange anything. Only if they ask for that, say our team "
        "will take care of it."
    )
    if profile.recording_notice:
        lines.append(
            "- Recording: your opening line asked if this call may be recorded. "
            "When they answer, call recording_consent once: agreed=true only for "
            "a clear yes; false for no, an unclear answer or no answer. Then go "
            "on with the call either way. If they later ask not to be recorded, "
            "call recording_consent with agreed=false."
        )
    if not profile.keep_transcript:
        # The organisation keeps nothing from this call: "noted" would be untrue.
        lines.append(
            "- Nothing said on this call is saved: never say you noted anything."
        )
    lines.append(
        "- Work towards the purpose; never ask what help the caller needs if you "
        "already know why you are talking."
    )
    if profile.business_info:
        lines.append(
            "- For any fact about the business (timings, address, services, fees), "
            "call get_business_info and use only what it returns."
        )
    # Django checks the size when publishing, so this should never happen. If
    # it does (a worker set lower than Django), drop whole lines from the end
    # rather than cut a sentence in half, and say so in the log.
    dropped = 0
    while len("\n".join(lines).encode("utf-8")) > budget and len(lines) > 2:
        lines.pop(-2 if profile.business_info and len(lines) > 3 else -1)
        dropped += 1
    if dropped:
        logger.warning(
            "profile section over budget; lines dropped",
            extra={"dropped_lines": dropped, "budget_bytes": budget},
        )
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
