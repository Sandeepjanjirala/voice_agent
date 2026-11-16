"""Lets a caller say "ఆ" or "ఊ" while the agent talks without losing the answer.

Speech from the caller pauses the agent at once (that is LiveKit's barge-in).
What decides whether the agent's reply is thrown away is the transcript that
follows. This filter sits on the transcript stream (the documented `stt_node`
hook) and, only while the agent has speech in progress, holds back a transcript
that is nothing but a short acknowledgement. With no transcript, LiveKit treats
the pause as a false interruption and resumes the reply.

Everything else passes untouched, so "ఆగండి", "stop", "bye", a question or any
other word still interrupts. If the agent's speech ends shortly after the
acknowledgement, the caller was probably answering a question ("ఆ" = yes), so
the held transcript is delivered after all.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterable, AsyncIterator, Callable

from livekit.agents import stt

# Sounds that mean "I am listening", not "I want to speak".
ACKNOWLEDGEMENTS = frozenset(
    {
        "ఆ", "ఆఁ", "ఆహా", "అహా", "హా", "హాఁ", "ఊ", "ఊఁ", "ఉఁ", "మ్", "ఉమ్", "హ్మ్",
        "ఓ", "ఓహ్", "ఓహో", "సరే", "ఓకే",
        "ah", "aa", "ha", "haa", "hm", "hmm", "mm", "mhm", "uh", "oh", "ok", "okay",
    }
)  # fmt: skip
MAX_ACKNOWLEDGEMENT_WORDS = 3
# How long a held acknowledgement waits for the agent to finish speaking.
HOLD_SECONDS = 3.0

_TRANSCRIPTS = (
    stt.SpeechEventType.INTERIM_TRANSCRIPT,
    stt.SpeechEventType.PREFLIGHT_TRANSCRIPT,
    stt.SpeechEventType.FINAL_TRANSCRIPT,
)
_PUNCTUATION = re.compile(r"[\s.,!?;:…\"'`\-–—।॥]+")


def is_acknowledgement(text: str) -> bool:
    """True for one to three acknowledgement sounds and nothing else."""
    words = [word for word in _PUNCTUATION.split(text.lower()) if word]
    return 0 < len(words) <= MAX_ACKNOWLEDGEMENT_WORDS and all(
        word in ACKNOWLEDGEMENTS for word in words
    )


def _text(event: stt.SpeechEvent) -> str:
    return event.alternatives[0].text if event.alternatives else ""


async def filter_acknowledgements(
    events: AsyncIterable[stt.SpeechEvent | str],
    *,
    agent_is_speaking: Callable[[], bool],
    hold_seconds: float = HOLD_SECONDS,
    poll_seconds: float = 0.1,
) -> AsyncIterator[stt.SpeechEvent | str]:
    queue: asyncio.Queue = asyncio.Queue()
    done = object()

    async def pump() -> None:
        try:
            async for event in events:
                await queue.put(event)
        finally:
            await queue.put(done)

    pump_task = asyncio.create_task(pump())
    held: stt.SpeechEvent | None = None
    held_at = 0.0
    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(), poll_seconds if held is not None else None
                )
            except TimeoutError:
                event = None

            if held is not None:
                if not agent_is_speaking():
                    # The agent finished: the caller was answering, not nodding.
                    yield held
                    held = None
                elif time.monotonic() - held_at > hold_seconds:
                    held = None

            if event is None:
                continue
            if event is done:
                break
            is_transcript = (
                isinstance(event, stt.SpeechEvent) and event.type in _TRANSCRIPTS
            )
            if (
                is_transcript
                and is_acknowledgement(_text(event))
                and agent_is_speaking()
            ):
                if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                    held, held_at = event, time.monotonic()
                continue
            if is_transcript and _text(event):
                # Real words followed: they speak for themselves.
                held = None
            yield event
    finally:
        pump_task.cancel()
