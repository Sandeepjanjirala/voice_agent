"""LiveKit Agents worker entrypoint for the Telugu calling agent.

This is a standalone process, separate from Django:

    lk agent console voice_agent/agent.py      # talk to it with your microphone
    lk agent dev voice_agent/agent.py          # connect it to LiveKit Cloud
    uv run voice_agent/agent.py start          # production mode

Inbound phone callers arrive as SIP participants through a LiveKit dispatch
rule (Phase 2, see apps/telephony); outbound dialling is Phase 3.
"""

import logging

from dotenv import load_dotenv
from livekit.agents import AgentServer, JobContext, cli, room_io
from livekit.plugins import ai_coustics

from voice_agent.assistant import TeluguAssistant, greet
from voice_agent.config import PROJECT_ROOT, ConfigError, load_settings
from voice_agent.observability import attach_session_observers, configure_logging
from voice_agent.pipeline import build_session
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

    session = build_session(settings)
    attach_session_observers(session, settings, session_id=ctx.room.name)

    audio_input = room_io.AudioInputOptions()
    if settings.noise_cancellation:
        # LiveKit Cloud only; set NOISE_CANCELLATION=false when self-hosting.
        audio_input = room_io.AudioInputOptions(
            noise_cancellation=ai_coustics.audio_enhancement(
                model=ai_coustics.EnhancerModel.QUAIL_VF_S
            ),
        )

    await session.start(
        agent=TeluguAssistant(settings),
        room=ctx.room,
        room_options=room_io.RoomOptions(audio_input=audio_input),
    )
    await ctx.connect()
    log_callers(ctx.room)

    # Speak first, but only once the room is connected (see greet()).
    greet(session, settings)


if __name__ == "__main__":
    cli.run_app(server)
