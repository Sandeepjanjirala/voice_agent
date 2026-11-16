"""LiveKit Agents worker entrypoint for the Telugu calling agent.

This is a standalone process, separate from Django:

    lk agent console voice_agent/agent.py      # talk to it with your microphone
    lk agent dev voice_agent/agent.py          # connect it to LiveKit Cloud
    uv run voice_agent/agent.py start          # production mode

Inbound phone callers arrive as SIP participants through a LiveKit dispatch
rule (Phase 2, see apps/telephony). A job whose metadata carries a dial
request places one outbound test call instead (see voice_agent/outbound.py).
"""

import logging

from dotenv import load_dotenv
from livekit.agents import AgentServer, JobContext, cli, room_io

from voice_agent.assistant import TeluguAssistant, greet
from voice_agent.config import PROJECT_ROOT, ConfigError, load_settings
from voice_agent.observability import attach_session_observers, configure_logging
from voice_agent.outbound import parse_dial_request, run_outbound_call
from voice_agent.pipeline import build_audio_input, build_session
from voice_agent.profile import parse_profile
from voice_agent.telephony import log_callers

logger = logging.getLogger("voice_agent")

load_dotenv(PROJECT_ROOT / ".env.local")

try:
    settings = load_settings()
except ConfigError as e:
    raise SystemExit(f"Voice agent configuration error: {e}") from None

configure_logging(settings)

server = AgentServer()


@server.rtc_session(agent_name=settings.agent_name)
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    logger.info("starting voice session", extra=settings.describe())

    # A job dispatched by `manage.py telephony call` places one phone call.
    profile = parse_profile(ctx.job.metadata)
    dial = parse_dial_request(ctx.job.metadata)
    if dial is not None:
        await run_outbound_call(ctx, settings, dial, profile)
        return

    session = build_session(settings)
    attach_session_observers(session, settings, session_id=ctx.room.name)

    await session.start(
        agent=TeluguAssistant(settings, profile),
        room=ctx.room,
        room_options=room_io.RoomOptions(audio_input=build_audio_input(settings)),
    )
    await ctx.connect()
    log_callers(ctx.room)

    # Speak first, but only once the room is connected (see greet()).
    greet(session, settings, profile=profile)


if __name__ == "__main__":
    cli.run_app(server)
