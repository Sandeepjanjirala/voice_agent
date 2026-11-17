"""Inbound phone calls: ask Django which AI employee answers, then run the call.

Used only when INBOUND_CONTEXT_URL is set. The worker posts the dialled number,
the caller's number and the room to Django, signed like call reports (see
call_reporting.py). Django answers with the profile of the published employee
assigned to the dialled number, or 404 when the number is not routed. The
caller's number goes to Django only, over this signed request, so that Django
can recognise a known contact; it is never logged here.

Nothing in this module opens a phone line. Calls reach it only through a
LiveKit dispatch rule, which stays off until the SIP trunk is authenticated.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import aiohttp
from livekit import api, rtc
from livekit.agents import JobContext, room_io

from .assistant import TeluguAssistant, greet
from .call_reporting import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    CallMetrics,
    CallReporter,
    sign,
)
from .config import VoiceSettings
from .observability import attach_session_observers
from .pipeline import build_audio_input, build_session
from .profile import PROFILE_KEY, AgentProfile, parse_profile, voice_for
from .telephony import describe_caller, is_phone_caller, log_callers

logger = logging.getLogger("voice_agent")

TIMEOUT_SECONDS = 3.0


async def fetch_profile(
    settings: VoiceSettings,
    room: str,
    participant: rtc.Participant,
    *,
    session_factory=aiohttp.ClientSession,
) -> AgentProfile | None:
    """The profile Django assigns to this call, or None. Never raises."""
    if not (settings.inbound_context_url and settings.call_events_secret):
        return None
    attributes = participant.attributes
    body = json.dumps(
        {
            "room": room,
            "called": attributes.get("sip.trunkPhoneNumber", ""),
            "caller": attributes.get("sip.phoneNumber", ""),
            "sip_call_id": attributes.get("sip.callID", ""),
        },
        separators=(",", ":"),
    ).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: sign(settings.call_events_secret, timestamp, body),
    }
    try:
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        async with (
            session_factory(timeout=timeout) as session,
            session.post(
                settings.inbound_context_url, data=body, headers=headers
            ) as response,
        ):
            if response.status != 200:
                logger.warning(
                    "no inbound route from Django",
                    extra={
                        **describe_caller(participant),
                        "http_status": response.status,
                    },
                )
                return None
            data: Any = await response.json()
    except Exception as error:
        logger.warning(
            "inbound context not available",
            extra={"error_type": type(error).__name__},
        )
        return None
    if not isinstance(data, dict):
        return None
    return parse_profile(json.dumps({PROFILE_KEY: data.get(PROFILE_KEY)}))


async def run_inbound_call(
    ctx: JobContext, settings: VoiceSettings, fallback: AgentProfile | None
) -> None:
    """Whole life of one call to a routed number, reported to Django."""
    await ctx.connect()
    log_callers(ctx.room)
    participant = await ctx.wait_for_participant()
    profile = fallback
    reporter = None
    if is_phone_caller(participant):
        profile = await fetch_profile(settings, ctx.room.name, participant) or fallback
        reporter = CallReporter.from_settings(settings, room=ctx.room.name)
    metrics = CallMetrics(keep_transcript=bool(profile and profile.keep_transcript))
    answered_at = time.monotonic()

    async def finish(reason: str = "") -> None:
        if reporter is not None and not reporter.ended:
            await reporter.send(
                "ended",
                outcome="completed",
                talk_seconds=round(time.monotonic() - answered_at, 1),
                **metrics.ended_fields(),
            )
        if reporter is not None:
            # The caller's leg goes with the room; nothing is left ringing.
            try:
                await ctx.api.room.delete_room(
                    api.DeleteRoomRequest(room=ctx.room.name)
                )
            except Exception as error:
                logger.warning(
                    "could not delete call room",
                    extra={"error_type": type(error).__name__},
                )

    ctx.add_shutdown_callback(finish)
    if reporter is not None:
        await reporter.send("answered")

    settings = voice_for(settings, profile)
    session = build_session(settings)
    attach_session_observers(session, settings, session_id=ctx.room.name)
    session.on("conversation_item_added", lambda ev: metrics.observe(ev.item))

    @session.on("close")
    def _on_close(_event) -> None:
        ctx.shutdown(reason="inbound call ended")

    @ctx.room.on("participant_disconnected")
    def _on_left(left: rtc.RemoteParticipant) -> None:
        if left.identity == participant.identity:
            ctx.shutdown(reason="caller hung up")

    await session.start(
        agent=TeluguAssistant(settings, profile),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            participant_identity=participant.identity,
            audio_input=build_audio_input(settings),
        ),
    )
    greet(session, settings, profile=profile)
