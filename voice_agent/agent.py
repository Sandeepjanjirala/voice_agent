"""LiveKit Agents worker entrypoint for the Telugu calling agent.

This is a standalone process, separate from Django:

    lk agent console voice_agent/agent.py      # talk to it with your microphone
    lk agent dev voice_agent/agent.py          # connect it to LiveKit Cloud
    uv run voice_agent/agent.py start          # production mode

Inbound phone callers arrive as SIP participants through a LiveKit dispatch
rule (Phase 2, see apps/telephony). With INBOUND_CONTEXT_URL set, the worker
asks Django which published AI employee answers the dialled number (see
voice_agent/inbound.py). A job whose metadata carries a dial request places
one outbound test call instead (see voice_agent/outbound.py).
"""

import logging
import os

from dotenv import load_dotenv
from livekit.agents import AgentServer, JobContext, cli, room_io

from voice_agent.assistant import TeluguAssistant, greet
from voice_agent.call_profile import fetch_call_profile
from voice_agent.config import PROJECT_ROOT, ConfigError, load_settings
from voice_agent.inbound import run_inbound_call
from voice_agent.observability import attach_session_observers, configure_logging
from voice_agent.outbound import parse_dial_request, run_outbound_call
from voice_agent.pipeline import build_audio_input, build_session
from voice_agent.profile import (
    load_profile_file,
    parse_profile,
    parse_profile_ref,
    recording_options,
    voice_for,
)
from voice_agent.telephony import log_callers

logger = logging.getLogger("voice_agent")

# Skipped under the test settings (SKIP_ENV_LOCAL), as in config/settings.py.
if not os.environ.get("SKIP_ENV_LOCAL", "").strip():
    load_dotenv(PROJECT_ROOT / ".env.local")

try:
    settings = load_settings()
except ConfigError as e:
    raise SystemExit(f"Voice agent configuration error: {e}") from None

configure_logging(settings)

# After a call the job reports the end (retried, about 17 s at worst), deletes
# the room and uploads any recording (3 tries of up to 60 s). LiveKit kills a
# job process that takes longer than this to shut down (default 10 s).
SHUTDOWN_SECONDS = 240.0
server = AgentServer(shutdown_process_timeout=SHUTDOWN_SECONDS)


@server.rtc_session(agent_name=settings.agent_name)
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    logger.info("starting voice session", extra=settings.describe())

    # A job dispatched by `manage.py telephony call` places one phone call.
    profile = parse_profile(ctx.job.metadata)
    dial = parse_dial_request(ctx.job.metadata)
    if dial is not None:
        # A call planned by Django carries a reference to its snapshot. If the
        # snapshot cannot be fetched, the call is not placed: it would
        # otherwise speak without the employee's instructions.
        ref = parse_profile_ref(ctx.job.metadata)
        if ref is not None:
            profile = await fetch_call_profile(settings, ref)
        await run_outbound_call(
            ctx,
            settings,
            dial,
            profile,
            profile_missing=ref is not None and not profile,
        )
        return
    # Local testing: a console or debugger session can play an employee from
    # a file. A dispatched job (a real room) never reads it.
    if profile is None and ctx.is_fake_job() and settings.console_profile_file:
        profile = load_profile_file(settings.console_profile_file)
    # Phone calls into a routed number take their employee from Django.
    if settings.inbound_context_url:
        await run_inbound_call(ctx, settings, profile)
        return

    voice = voice_for(settings, profile)
    session = build_session(voice)
    attach_session_observers(session, voice, session_id=ctx.room.name)

    await session.start(
        agent=TeluguAssistant(voice, profile),
        room=ctx.room,
        room_options=room_io.RoomOptions(audio_input=build_audio_input(settings)),
        **recording_options(profile),
    )
    await ctx.connect()
    log_callers(ctx.room)

    # Speak first, but only once the room is connected (see greet()).
    greet(session, voice, profile=profile)


if __name__ == "__main__":
    cli.run_app(server)
