"""Places one outbound phone call when the job asks for it.

The dial request arrives as job metadata from Django (`manage.py telephony
call`). The worker dials only the single number in OUTBOUND_TEST_ALLOWED_NUMBER,
starts the conversation after the callee answers, and deletes the room when the
call is over so the phone line is always released. No retries. The outcome is
reported to Django when CALL_EVENTS_URL is set (see call_reporting.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

from google.protobuf.duration_pb2 import Duration
from livekit import api, rtc
from livekit.agents import JobContext, room_io

from .assistant import TeluguAssistant, greet
from .call_reporting import CallMetrics, CallReporter
from .config import VoiceSettings
from .observability import attach_session_observers
from .pipeline import build_audio_input, build_session
from .profile import AgentProfile
from .telephony import log_callers, mask_phone

logger = logging.getLogger("voice_agent")

METADATA_KEY = "outbound_call"
CALLEE_IDENTITY = "callee"

# Outcome names written to the log; one per finished dial attempt.
ANSWERED = "answered"
BUSY_OR_REJECTED = "busy_or_rejected"
NO_ANSWER = "no_answer"
FAILED = "failed"
REFUSED = "refused"
EXPIRED = "expired"

# A dial request older than this is not dialled: nobody is waiting for it.
MAX_REQUEST_AGE_SECONDS = 60


@dataclass(frozen=True)
class DialRequest:
    to: str
    trunk_id: str
    ringing_timeout: int
    max_call_duration: int
    requested_at: float | None = None


@dataclass(frozen=True)
class DialResult:
    outcome: str
    sip_status_code: int | None = None
    sip_status: str | None = None


def parse_dial_request(metadata: str | None) -> DialRequest | None:
    """The dial request in job metadata, or None for every other kind of job."""
    try:
        data = json.loads(metadata) if metadata else None
    except ValueError:
        return None
    request = data.get(METADATA_KEY) if isinstance(data, dict) else None
    if not isinstance(request, dict):
        return None
    try:
        return DialRequest(
            to=str(request["to"]),
            trunk_id=str(request["trunk_id"]),
            # Clamped here as well, so a bad request cannot keep a line open.
            ringing_timeout=min(max(int(request.get("ringing_timeout", 30)), 10), 60),
            max_call_duration=min(
                max(int(request.get("max_call_duration", 300)), 30), 600
            ),
            requested_at=float(request["requested_at"])
            if request.get("requested_at") is not None
            else None,
        )
    except (KeyError, TypeError, ValueError):
        return DialRequest(to="", trunk_id="", ringing_timeout=30, max_call_duration=30)


def classify_failure(error: Exception) -> str:
    code = getattr(error, "sip_status_code", None)
    if code in (486, 600, 603):
        return BUSY_OR_REJECTED
    if code in (408, 480, 487):
        return NO_ANSWER
    return FAILED


async def place_call(
    ctx: JobContext, dial: DialRequest, settings: VoiceSettings
) -> DialResult:
    """Dial and wait for an answer. Never raises."""
    callee = mask_phone(dial.to)
    allowed = settings.outbound_allowed_number
    if not (dial.to and dial.trunk_id and allowed and dial.to == allowed):
        logger.error(
            "outbound call refused: number is not the allowed test number",
            extra={"callee": callee},
        )
        return DialResult(REFUSED)
    age = time.time() - dial.requested_at if dial.requested_at is not None else 0.0
    if age > MAX_REQUEST_AGE_SECONDS:
        logger.warning(
            "outbound call refused: request is too old",
            extra={"callee": callee, "age_seconds": round(age)},
        )
        return DialResult(EXPIRED)

    logger.info("dialling", extra={"callee": callee, "sip_trunk_id": dial.trunk_id})
    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=dial.trunk_id,
                sip_call_to=dial.to,
                participant_identity=CALLEE_IDENTITY,
                # Keep the callee's number out of participant attributes and logs.
                hide_phone_number=True,
                ringing_timeout=Duration(seconds=dial.ringing_timeout),
                max_call_duration=Duration(seconds=dial.max_call_duration),
                wait_until_answered=True,
            )
        )
    except Exception as error:
        result = DialResult(
            outcome=classify_failure(error),
            sip_status_code=getattr(error, "sip_status_code", None),
            sip_status=getattr(error, "sip_status", None),
        )
        logger.warning(
            "outbound call did not connect",
            extra={
                "callee": callee,
                "outcome": result.outcome,
                "sip_status_code": result.sip_status_code,
                "sip_status": result.sip_status,
                "error_type": type(error).__name__,
            },
        )
        return result
    logger.info("outbound call answered", extra={"callee": callee})
    return DialResult(ANSWERED)


async def run_outbound_call(
    ctx: JobContext,
    settings: VoiceSettings,
    dial: DialRequest,
    profile: AgentProfile | None = None,
) -> str:
    """Whole life of one outbound call. Returns the dial outcome."""
    await ctx.connect()
    log_callers(ctx.room)
    reporter = CallReporter.from_settings(settings, room=ctx.room.name)
    metrics = CallMetrics()
    call: dict = {"answered_at": None, "tasks": set()}

    async def hang_up(reason: str = "") -> None:
        if call["answered_at"] is not None and not reporter.ended:
            await reporter.send(
                "ended",
                outcome="completed",
                talk_seconds=round(time.monotonic() - call["answered_at"], 1),
                **metrics.summary(),
            )
        # Deleting the room disconnects the phone leg whatever state it is in.
        try:
            await ctx.api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
        except Exception as error:
            logger.warning(
                "could not delete call room", extra={"error_type": type(error).__name__}
            )

    ctx.add_shutdown_callback(hang_up)

    result = await place_call(ctx, dial, settings)
    if result.outcome != ANSWERED:
        # Reported before shutdown, while the room still exists.
        await reporter.send(
            "ended",
            outcome=result.outcome,
            sip_status_code=result.sip_status_code,
            sip_status=result.sip_status,
        )
        ctx.shutdown(reason=f"outbound call {result.outcome}")
        return result.outcome

    call["answered_at"] = time.monotonic()
    # Not awaited: the callee must not wait in silence for Django.
    task = asyncio.create_task(reporter.send("answered"))
    call["tasks"].add(task)
    task.add_done_callback(call["tasks"].discard)

    participant = await ctx.wait_for_participant(identity=CALLEE_IDENTITY)

    # Only now, with the callee on the line, does the conversation start.
    session = build_session(settings)
    attach_session_observers(session, settings, session_id=ctx.room.name)
    session.on("conversation_item_added", lambda ev: metrics.observe(ev.item))

    @session.on("close")
    def _on_close(_event) -> None:
        ctx.shutdown(reason="outbound call ended")

    @ctx.room.on("participant_disconnected")
    def _on_left(left: rtc.RemoteParticipant) -> None:
        if left.identity == CALLEE_IDENTITY:
            ctx.shutdown(reason="callee hung up")

    await session.start(
        agent=TeluguAssistant(settings, profile),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            participant_identity=participant.identity,
            audio_input=build_audio_input(settings),
        ),
    )
    # People say "hello" as they answer. That must not cut the greeting off.
    greet(session, settings, allow_interruptions=False, profile=profile)
    return result.outcome
