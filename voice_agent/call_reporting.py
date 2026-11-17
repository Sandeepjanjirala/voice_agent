"""Tells Django how a phone call went: answered, ended, SIP status, latency.

The worker never imports Django. It posts a small JSON message, signed with
HMAC-SHA256, to CALL_EVENTS_URL. The message carries the room name, the outcome
and timings, and never a phone number. What was said is sent only when the
call's profile allows it (its organisation keeps transcripts), with any
number-like text masked. With CALL_EVENTS_URL or CALL_EVENTS_SECRET unset,
reporting is off and Django infers the outcome from LiveKit room state instead.
"""

from __future__ import annotations

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
# A transcript is cut to fit one report; Django accepts up to 64 kB.
TRANSCRIPT_TURNS = 80
TRANSCRIPT_TURN_CHARS = 600
TRANSCRIPT_TOTAL_CHARS = 24_000


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """Signature over "<timestamp>.<body>", so a captured message cannot be
    replayed later with a fresh timestamp."""
    message = timestamp.encode() + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


class CallMetrics:
    """Collects per-turn timings during a call and summarises them at the end.
    With `keep_transcript`, it also keeps what each side said, masked."""

    def __init__(self, *, keep_transcript: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self.keep_transcript = keep_transcript
        self.turns: list[dict[str, str]] = []

    def observe(self, item: Any) -> None:
        row = turn_row(item)
        if row is not None:
            self.rows.append(row)
        if self.keep_transcript:
            role = getattr(item, "role", None)
            text = getattr(item, "text_content", None)
            if role in ("user", "assistant") and isinstance(text, str) and text.strip():
                self.turns.append({"role": role, "text": text.strip()})

    def transcript(self) -> list[dict[str, str]]:
        """The kept turns, masked and cut to the report's size limits."""
        kept, total = [], 0
        for turn in self.turns[:TRANSCRIPT_TURNS]:
            text = mask_phones_in(turn["text"])[:TRANSCRIPT_TURN_CHARS]
            if total + len(text) > TRANSCRIPT_TOTAL_CHARS:
                break
            total += len(text)
            kept.append({"role": turn["role"], "text": text})
        return kept

    def ended_fields(self) -> dict[str, Any]:
        """Everything the "ended" report carries about the conversation."""
        fields = self.summary()
        if self.keep_transcript:
            fields["transcript"] = self.transcript()
        return fields

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
        if event == "ended":
            self.ended = True
        if not self.enabled:
            return False
        body = json.dumps(
            {"room": self._room, "event": event, **fields}, separators=(",", ":")
        ).encode()
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: sign(self._secret, timestamp, body),
        }
        try:
            timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
            async with (
                self._session_factory(timeout=timeout) as session,
                session.post(self._url, data=body, headers=headers) as response,
            ):
                if response.status >= 300:
                    logger.warning(
                        "call event rejected by Django",
                        extra={"call_event": event, "http_status": response.status},
                    )
                    return False
        except Exception as error:
            logger.warning(
                "call event not delivered",
                extra={"call_event": event, "error_type": type(error).__name__},
            )
            return False
        return True
