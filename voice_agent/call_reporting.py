"""Tells Django how a phone call went: answered, ended, SIP status, latency.

The worker never imports Django. It posts a small JSON message, signed with
HMAC-SHA256, to CALL_EVENTS_URL. The message carries the room name, the outcome
and timings, and never a phone number. What was said is sent only when the
call's profile allows it (its organisation keeps transcripts), with any
number-like text masked. With CALL_EVENTS_URL or CALL_EVENTS_SECRET unset,
reporting is off and Django infers the outcome from LiveKit room state instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import statistics
import time
from typing import Any

import aiohttp

from .config import VoiceSettings
from .observability import turn_row
from .telephony import mask_phones_in

logger = logging.getLogger("voice_agent")

SIGNATURE_HEADER = "X-Call-Signature"
TIMESTAMP_HEADER = "X-Call-Timestamp"
TIMEOUT_SECONDS = 2.0
# A transcript is cut to fit one report: Django accepts up to 64 kB, and the
# report is sent as UTF-8 (a Telugu letter is 3 bytes), leaving room for the
# other fields.
TRANSCRIPT_TURNS = 120
TRANSCRIPT_TURN_CHARS = 600
TRANSCRIPT_TOTAL_BYTES = 48_000
# The "ended" report is the only copy of the conversation: it is retried.
ENDED_ATTEMPTS = 3
ENDED_TIMEOUT_SECONDS = 5.0
# Why a call ended, as reported to Django.
END_REASONS = (
    "caller_hung_up",
    "agent_ended",
    "time_limit",
    "error",
    "system",
)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """Signature over "<timestamp>.<body>", so a captured message cannot be
    replayed later with a fresh timestamp."""
    message = timestamp.encode() + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


class CallMetrics:
    """Collects per-turn timings during a call and summarises them at the end.

    With `keep_transcript`, it also keeps the conversation as it happened: what
    each side said (as speech recognition heard the caller), seconds from the
    start of the conversation, whether the agent was cut off, how sure the
    speech recognition was, and the tools the agent used (name and whether it
    worked, never their arguments). Phone numbers are masked."""

    def __init__(
        self, *, keep_transcript: bool = False, started_at: float | None = None
    ) -> None:
        self.rows: list[dict[str, Any]] = []
        self.keep_transcript = keep_transcript
        self.turns: list[dict[str, Any]] = []
        self.started_at = time.time() if started_at is None else started_at
        self.end_reason = ""
        self._end_hint = ""
        self.tools_used: set[str] = set()

    def _at(self, created_at: Any) -> float:
        try:
            return round(max(float(created_at) - self.started_at, 0.0), 1)
        except (TypeError, ValueError):
            return round(max(time.time() - self.started_at, 0.0), 1)

    def observe(self, item: Any) -> None:
        row = turn_row(item)
        if row is not None:
            self.rows.append(row)
        if self.keep_transcript:
            role = getattr(item, "role", None)
            text = getattr(item, "text_content", None)
            if role in ("user", "assistant") and isinstance(text, str) and text.strip():
                turn: dict[str, Any] = {
                    "role": role,
                    "text": text.strip(),
                    "t": self._at(getattr(item, "created_at", None)),
                }
                if getattr(item, "interrupted", False):
                    turn["interrupted"] = True
                confidence = getattr(item, "transcript_confidence", None)
                if role == "user" and isinstance(confidence, int | float):
                    turn["confidence"] = round(min(max(float(confidence), 0.0), 1.0), 2)
                self.turns.append(turn)

    def observe_tools(self, event: Any) -> None:
        """A batch of tools the agent ran (the `function_tools_executed` event)."""
        calls = getattr(event, "function_calls", None) or []
        outputs = getattr(event, "function_call_outputs", None) or []
        for index, call in enumerate(calls):
            name = str(getattr(call, "name", "") or "")[:40]
            if not name:
                continue
            self.tools_used.add(name)
            if not self.keep_transcript:
                continue
            output = outputs[index] if index < len(outputs) else None
            self.turns.append(
                {
                    "role": "tool",
                    "name": name,
                    "ok": not bool(getattr(output, "is_error", False)),
                    "t": self._at(getattr(call, "created_at", None)),
                }
            )

    def transcript(self) -> list[dict[str, Any]]:
        """The kept turns, masked and cut to the report's size limits."""
        kept, total = [], 0
        for turn in self.turns[:TRANSCRIPT_TURNS]:
            turn = dict(turn)
            if "text" in turn:
                turn["text"] = mask_phones_in(turn["text"])[:TRANSCRIPT_TURN_CHARS]
            size = len(json.dumps(turn, ensure_ascii=False).encode())
            if total + size > TRANSCRIPT_TOTAL_BYTES:
                break
            total += size
            kept.append(turn)
        return kept

    def ended_fields(self) -> dict[str, Any]:
        """Everything the "ended" report carries about the conversation."""
        fields = self.summary()
        if self.end_reason in END_REASONS:
            fields["end_reason"] = self.end_reason
        if self.keep_transcript:
            fields["transcript"] = self.transcript()
        return fields

    def on_close(self, event: Any) -> None:
        """The session's `close` event."""
        raw = getattr(event, "reason", "")
        reason = str(getattr(raw, "value", raw) or "")
        if getattr(event, "error", None) is not None or reason == "error":
            self.note_end("error")
        elif reason == "participant_disconnected":
            self.note_end("caller_hung_up")

    def note_end(self, reason: str) -> None:
        """What was seen first as the call ended: the caller leaving, or the
        session closing (with an error, if there was one)."""
        if not self._end_hint and reason in END_REASONS:
            self._end_hint = reason

    def finish(self, *, talk_seconds: float, limit: int = 0) -> None:
        """Settle why the call ended. The agent's own end_call wins; a caller
        leg that drops at the time limit is the time limit, not the caller."""
        if self.end_reason:
            return
        hint = self._end_hint or "system"
        if "end_call" in self.tools_used:
            self.end_reason = "agent_ended"
        elif hint == "caller_hung_up" and limit and talk_seconds >= limit - 2:
            self.end_reason = "time_limit"
        else:
            self.end_reason = hint

    def summary(self) -> dict[str, Any]:
        agent = [r for r in self.rows if r.get("role") == "assistant"]
        e2e = [r["e2e_latency"] for r in agent if r.get("e2e_latency") is not None]
        ttft = [r["llm_node_ttft"] for r in agent if r.get("llm_node_ttft") is not None]
        ttfb = [r["tts_node_ttfb"] for r in agent if r.get("tts_node_ttfb") is not None]
        return {
            "agent_turns": len(agent),
            "user_turns": sum(1 for r in self.rows if r.get("role") == "user"),
            "interruptions": sum(1 for r in agent if r.get("interrupted")),
            "e2e_latency_p50": _median(e2e),
            "e2e_latency_max": round(max(e2e), 3) if e2e else None,
            "llm_ttft_p50": _median(ttft),
            "tts_ttfb_p50": _median(ttfb),
        }


class CallReporter:
    """Posts call events for one room. Never raises into the call."""

    def __init__(
        self,
        url: str | None,
        secret: str | None,
        room: str,
        *,
        session_factory=aiohttp.ClientSession,
    ) -> None:
        self._url = url
        self._secret = secret
        self._room = room
        self._session_factory = session_factory
        self.ended = False

    @classmethod
    def from_settings(cls, settings: VoiceSettings, room: str) -> CallReporter:
        return cls(settings.call_events_url, settings.call_events_secret, room)

    @property
    def enabled(self) -> bool:
        return bool(self._url and self._secret)

    async def send(self, event: str, **fields: Any) -> bool:
        """Post one event. The "ended" report carries the conversation and is
        tried up to ENDED_ATTEMPTS times; Django applies it only once."""
        if event == "ended":
            self.ended = True
        if not self.enabled:
            return False
        # UTF-8, not \u escapes: a Telugu letter is 3 bytes instead of 6.
        body = json.dumps(
            {"room": self._room, "event": event, **fields},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        # The end of a call and every recording decision are retried: losing
        # either would leave the call page wrong.
        retried = event in ("ended", "recording")
        attempts = ENDED_ATTEMPTS if retried else 1
        seconds = ENDED_TIMEOUT_SECONDS if retried else TIMEOUT_SECONDS
        for attempt in range(1, attempts + 1):
            status = await self._post(event, body, seconds)
            if status is not None and status < 300:
                return True
            # A refusal (bad signature, too large, unknown room) will not change.
            if status is not None and status < 500:
                return False
            if attempt < attempts:
                await asyncio.sleep(0.5 * attempt)
        return False

    async def _post(self, event: str, body: bytes, seconds: float) -> int | None:
        """The HTTP status, or None if Django could not be reached."""
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: sign(self._secret, timestamp, body),
        }
        try:
            timeout = aiohttp.ClientTimeout(total=seconds)
            async with (
                self._session_factory(timeout=timeout) as session,
                session.post(self._url, data=body, headers=headers) as response,
            ):
                if response.status >= 300:
                    logger.warning(
                        "call event rejected by Django",
                        extra={"call_event": event, "http_status": response.status},
                    )
                return response.status
        except Exception as error:
            logger.warning(
                "call event not delivered",
                extra={"call_event": event, "error_type": type(error).__name__},
            )
            return None
