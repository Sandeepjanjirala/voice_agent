"""Session logging, per-turn latency capture and the spoken error fallback.

Per-turn latency is read from `ChatMessage.metrics` (the `metrics_collected`
event is deprecated in livekit-agents 1.8) and appended to a JSONL file that
`scripts/latency_report.py` summarises.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from livekit.agents import AgentSession

from .config import VoiceSettings

logger = logging.getLogger("voice_agent")

# Seconds-valued keys of livekit.agents.llm.MetricsReport worth keeping.
LATENCY_KEYS = (
    "transcription_delay",
    "end_of_turn_delay",
    "llm_node_ttft",
    "llm_node_ttfs",
    "tts_node_ttfb",
    "e2e_latency",
)

FALLBACK_MIN_INTERVAL_S = 8.0


def configure_logging(settings: VoiceSettings) -> None:
    """Set our logger's level; handlers and format belong to the LiveKit CLI."""
    logger.setLevel(settings.log_level)


class TurnMetricsWriter:
    """Appends one JSON line per conversation turn; never raises into a call."""

    def __init__(self, path: Path | None, session_id: str) -> None:
        self._path = path
        self._session_id = session_id

    def write(self, row: dict[str, Any]) -> None:
        if self._path is None:
            return
        record = {"ts": round(time.time(), 3), "session": self._session_id, **row}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("could not write turn metrics")


def events_log_path(metrics_path: Path | None) -> Path | None:
    """Timing events sit next to the turn metrics, and are off when they are."""
    return None if metrics_path is None else metrics_path.with_name("call_events.jsonl")


def turn_row(item: Any) -> dict[str, Any] | None:
    """Reduce a chat item to a metrics row, or None if it is not a spoken turn."""
    if getattr(item, "type", None) != "message":
        return None
    if item.role not in ("user", "assistant"):
        return None
    metrics = item.metrics or {}
    row: dict[str, Any] = {
        "role": item.role,
        "chars": len(item.text_content or ""),
        "interrupted": bool(item.interrupted),
    }
    for key in LATENCY_KEYS:
        if (value := metrics.get(key)) is not None:
            row[key] = round(float(value), 3)
    return row


class FallbackSpeaker:
    """Speaks a fixed apology when the STT or LLM fails for good.

    Without this the caller hears silence until the session gives up. It is
    rate-limited so a failing provider cannot make the agent apologise in a
    loop, and it stays quiet on TTS errors because speaking is what failed.
    """

    def __init__(self, session: AgentSession, message: str) -> None:
        self._session = session
        self._message = message
        self._last_spoken: float | None = None
        self.spoken_count = 0

    def on_error(self, ev: Any) -> None:
        error = ev.error
        error_type = getattr(error, "type", "unknown")
        recoverable = bool(getattr(error, "recoverable", False))
        logger.log(
            logging.WARNING if recoverable else logging.ERROR,
            "pipeline error",
            extra={
                "error_type": error_type,
                "recoverable": recoverable,
                "source": getattr(error, "label", None),
                "detail": str(getattr(error, "error", error)),
            },
        )
        if recoverable or error_type == "tts_error":
            return

        now = time.monotonic()
        if (
            self._last_spoken is not None
            and now - self._last_spoken < FALLBACK_MIN_INTERVAL_S
        ):
            return
        self._last_spoken = now
        try:
            self._session.say(self._message, add_to_chat_ctx=False)
            self.spoken_count += 1
        except RuntimeError:
            # The session is already closing; nothing left to say it with.
            logger.warning("fallback message not spoken: session is closing")


def attach_session_observers(
    session: AgentSession, settings: VoiceSettings, *, session_id: str
) -> FallbackSpeaker:
    writer = TurnMetricsWriter(settings.metrics_log_path, session_id)
    fallback = FallbackSpeaker(session, settings.fallback_message)

    @session.on("user_input_transcribed")
    def _on_transcript(ev: Any) -> None:
        if not ev.is_final:
            return
        extra: dict[str, Any] = {
            "language": str(ev.language) if ev.language else None,
            "chars": len(ev.transcript),
        }
        if settings.log_transcripts:
            extra["transcript"] = ev.transcript
        logger.info("user transcript", extra=extra)

    @session.on("conversation_item_added")
    def _on_item(ev: Any) -> None:
        row = turn_row(ev.item)
        if row is None:
            return
        writer.write(row)
        events.write(
            {
                "event": "turn",
                "role": row.get("role"),
                "chars": row.get("chars"),
                "interrupted": row.get("interrupted"),
            }
        )
        extra = dict(row)
        if settings.log_transcripts:
            extra["text"] = ev.item.text_content
        logger.info("turn completed", extra=extra)

    # Timing-only event trail for diagnosing broken-up speech: when the agent
    # and caller start and stop talking, and every overlap the interruption
    # logic acted on. Never contains what was said.
    events = TurnMetricsWriter(events_log_path(settings.metrics_log_path), session_id)

    @session.on("agent_state_changed")
    def _on_agent_state(ev: Any) -> None:
        events.write({"event": "agent_state", "old": ev.old_state, "new": ev.new_state})

    @session.on("user_state_changed")
    def _on_user_state(ev: Any) -> None:
        events.write({"event": "user_state", "old": ev.old_state, "new": ev.new_state})

    @session.on("speech_created")
    def _on_speech_created(ev: Any) -> None:
        events.write({"event": "speech_created", "source": ev.source})

    @session.on("overlapping_speech")
    def _on_overlap(ev: Any) -> None:
        events.write(
            {
                "event": "overlapping_speech",
                "is_interruption": ev.is_interruption,
                "agent_ended": getattr(ev, "agent_ended", None),
                "total_duration": round(getattr(ev, "total_duration", 0.0), 3),
            }
        )

    @session.on("agent_false_interruption")
    def _on_false_interruption(ev: Any) -> None:
        events.write({"event": "false_interruption", "resumed": ev.resumed})
        logger.info("false interruption", extra={"resumed": ev.resumed})

    @session.on("close")
    def _on_close(ev: Any) -> None:
        logger.info(
            "session closed",
            extra={"reason": str(ev.reason), "had_error": ev.error is not None},
        )

    session.on("error", fallback.on_error)
    return fallback
