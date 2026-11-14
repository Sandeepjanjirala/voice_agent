"""Recognises phone callers and logs who joined without logging their number."""

from __future__ import annotations

import logging
import re

from livekit import rtc

logger = logging.getLogger("voice_agent")


def mask_phone(number: str | None) -> str:
    """Keep the country prefix and the last two digits: +91********10."""
    if not number:
        return "(hidden)"
    digits = re.sub(r"\D", "", number)
    if len(digits) < 7:
        return "*" * len(digits)
    plus = "+" if number.strip().startswith("+") else ""
    return f"{plus}{digits[:2]}{'*' * (len(digits) - 4)}{digits[-2:]}"


def is_phone_caller(participant: rtc.Participant) -> bool:
    return participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP


def describe_caller(participant: rtc.Participant) -> dict[str, str]:
    """Log-safe summary of a participant. Never contains a full phone number."""
    if not is_phone_caller(participant):
        return {"channel": "web"}
    attributes = participant.attributes
    return {
        "channel": "sip",
        "caller": mask_phone(attributes.get("sip.phoneNumber")),
        "dialled": mask_phone(attributes.get("sip.trunkPhoneNumber")),
        # LiveKit's own call ID, for matching against the telephony dashboard.
        "sip_call_id": attributes.get("sip.callID", ""),
        "sip_trunk_id": attributes.get("sip.trunkID", ""),
    }


def log_callers(room: rtc.Room) -> None:
    """Log each participant as it joins or leaves.

    Does not wait for anyone, so it is safe in console and text modes where no
    remote participant may ever appear.
    """

    def joined(participant: rtc.RemoteParticipant) -> None:
        logger.info("participant joined", extra=describe_caller(participant))

    def left(participant: rtc.RemoteParticipant) -> None:
        reason = rtc.DisconnectReason.Name(participant.disconnect_reason)
        logger.info(
            "participant left",
            extra={**describe_caller(participant), "disconnect_reason": reason},
        )

    for participant in room.remote_participants.values():
        joined(participant)
    room.on("participant_connected", joined)
    room.on("participant_disconnected", left)
