"""Talk to the agent in text, with an agent profile, against the real LLM.

    python scripts/try_profile.py                      # the built-in example
    python scripts/try_profile.py "హలో" "రేపు morning" # your own caller lines

Uses the active profile from the Django database if there is one, otherwise the
appointment-enquiry example. No audio, no phone call; it spends a little LLM
inference. Shows each reply, any tool call, and the time to first token.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env.local")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from livekit.agents import AgentSession  # noqa: E402
from livekit.agents.utils import http_context  # noqa: E402

from voice_agent.assistant import TeluguAssistant  # noqa: E402
from voice_agent.config import load_settings  # noqa: E402
from voice_agent.pipeline import build_llm  # noqa: E402
from voice_agent.profile import parse_profile  # noqa: E402

DEFAULT_LINES = [
    "ఆ, చెప్పండి.",
    "అవును, appointment కావాలి.",
    "రేపు morning పది గంటలకి.",
    "మీ office timings ఏంటి?",
    "సరే, thank you, bye.",
]


def profile_metadata() -> str:
    """Read through Django, in this script only; the worker itself never does."""
    import django

    django.setup()
    from apps.telephony import profiles

    return json.dumps(
        profiles.profile_metadata()
        or {
            profiles.PROFILE_KEY: profiles.EXAMPLE
            | {"purpose": profiles.EXAMPLE["call_purpose"]}
        }
    )


async def main(lines: list[str], metadata: str) -> None:
    settings = load_settings({**os.environ, "VOICE_METRICS_LOG": "off"})
    profile = parse_profile(metadata)
    print("greeting:", (profile.greeting if profile else "") or settings.greeting, "\n")
    async with (
        http_context.open(),
        AgentSession(llm=build_llm(settings.llm)) as session,
    ):
        await session.start(TeluguAssistant(settings, profile))
        for line in lines:
            result = await session.run(user_input=line)
            print("caller:", line)
            for event in result.events:
                if event.type == "function_call":
                    print("  [tool]", event.item.name)
                elif event.type == "message" and event.item.role == "assistant":
                    ttft = (event.item.metrics or {}).get("llm_node_ttft")
                    timing = f"  ({ttft:.2f} s)" if ttft else ""
                    print("agent: ", event.item.text_content, timing)
            print()
            if any(
                e.type == "function_call" and e.item.name == "end_call"
                for e in result.events
            ):
                break


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or DEFAULT_LINES, profile_metadata()))
