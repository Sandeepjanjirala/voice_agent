"""Fetch a phone call's employee profile from Django by signed reference.

Django dispatches an outbound call with a short-lived, signed reference to the
call's immutable snapshot instead of the instructions themselves (LiveKit
stores job metadata). The worker posts the reference back, signed with
CALL_EVENTS_SECRET like every other report, and gets exactly the profile the
call was planned with. Never raises.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import aiohttp

from .call_reporting import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign
from .config import VoiceSettings
from .profile import PROFILE_KEY, AgentProfile, parse_profile

logger = logging.getLogger("voice_agent")

TIMEOUT_SECONDS = 4.0


async def fetch_call_profile(
    settings: VoiceSettings,
    ref: dict[str, str],
    *,
    session_factory=aiohttp.ClientSession,
) -> AgentProfile | None:
    """The call's profile, or None if Django cannot be reached or refuses."""
    if not (settings.call_profile_url and settings.call_events_secret):
        logger.warning("call profile by reference, but CALL_PROFILE_URL is not set")
        return None
    body = json.dumps(ref, separators=(",", ":")).encode()
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
                settings.call_profile_url, data=body, headers=headers
            ) as response,
        ):
            if response.status != 200:
                logger.warning(
                    "call profile refused by Django",
                    extra={"http_status": response.status},
                )
                return None
            data: Any = await response.json()
    except Exception as error:
        logger.warning(
            "call profile not available", extra={"error_type": type(error).__name__}
        )
        return None
    if not isinstance(data, dict):
        return None
    return parse_profile(json.dumps({PROFILE_KEY: data.get(PROFILE_KEY)}))
