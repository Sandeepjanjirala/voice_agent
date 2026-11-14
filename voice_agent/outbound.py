"""Places one outbound phone call when the job asks for it.

The dial request arrives as job metadata from Django (`manage.py telephony
call`). The worker dials only the single number in OUTBOUND_TEST_ALLOWED_NUMBER,
starts the conversation after the callee answers, and deletes the room when the
call is over so the phone line is always released. No retries.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from google.protobuf.duration_pb2 import Duration
from livekit import api, rtc
from livekit.agents import JobContext, room_io

from .assistant import TeluguAssistant, greet
from .config import VoiceSettings
from .observability import attach_session_observers
from .pipeline import build_session
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


@dataclass(frozen=True)
class DialRequest:
    to: str
    trunk_id: str
    ringing_timeout: int
    max_call_duration: int


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
) -> str:
    """Dial and wait for an answer. Returns an outcome name; never raises."""
    callee = mask_phone(dial.to)
    allowed = settings.outbound_allowed_number
    if not (dial.to and dial.trunk_id and allowed and dial.to == allowed):
        logger.error(
            "outbound call refused: number is not the allowed test number",
            extra={"callee": callee},
        )
        return REFUSED

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
        outcome = classify_failure(error)
        logger.warning(
            "outbound call did not connect",
            extra={
                "callee": callee,
                "outcome": outcome,
                "sip_status_code": getattr(error, "sip_status_code", None),
                "sip_status": getattr(error, "sip_status", None),
                "error_type": type(error).__name__,
            },
        )
        return outcome
    logger.info("outbound call answered", extra={"callee": callee})
    return ANSWERED


async def run_outbound_call(
    ctx: JobContext, settings: VoiceSettings, dial: DialRequest
) -> str:
    """Whole life of one outbound call. Returns the dial outcome."""
    await ctx.connect()
    log_callers(ctx.room)

    async def hang_up(reason: str = "") -> None:
        # Deleting the room disconnects the phone leg whatever state it is in.
        try:
            await ctx.api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
        except Exception as error:
            logger.warning(
                "could not delete call room", extra={"error_type": type(error).__name__}
            )

    ctx.add_shutdown_callback(hang_up)

    outcome = await place_call(ctx, dial, settings)
    if outcome != ANSWERED:
        ctx.shutdown(reason=f"outbound call {outcome}")
        return outcome

    participant = await ctx.wait_for_participant(identity=CALLEE_IDENTITY)

    # Only now, with the callee on the line, does the conversation start.
    session = build_session(settings)
    attach_session_observers(session, settings, session_id=ctx.room.name)

    @session.on("close")
    def _on_close(_event) -> None:
        ctx.shutdown(reason="outbound call ended")

    @ctx.room.on("participant_disconnected")
    def _on_left(left: rtc.RemoteParticipant) -> None:
        if left.identity == CALLEE_IDENTITY:
            ctx.shutdown(reason="callee hung up")

    await session.start(
        agent=TeluguAssistant(settings),
        room=ctx.room,
        room_options=room_io.RoomOptions(participant_identity=participant.identity),
    )
    greet(session, settings)
    return outcome
