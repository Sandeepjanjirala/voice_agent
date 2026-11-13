"""Compare LiveKit Inference LLMs on the agent's own prompt (live, costs a few calls).

    uv run python scripts/llm_bakeoff.py google/gemma-4-31b-it openai/gpt-5.4-mini

Prints each model's replies to Telugu, English and Tenglish turns and its time
to first token. Needs LIVEKIT_* in .env.local. Read the replies as well as the
timings: a fast model that writes poor Telugu is not an improvement.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import inference, llm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_agent.prompts import build_instructions

TURNS = [
    "నమస్తే, మీరు ఎవరు?",
    "Hello, can you speak in English?",
    "నాకు రేపు morning ఒక appointment కావాలి, possible ఆ?",
    "మీ office address ఏంటి, fee ఎంత?",
    "naaku repu appointment kavali",
]
ROUNDS = 2


async def ask(model: str, text: str) -> tuple[float | None, str]:
    ctx = llm.ChatContext()
    ctx.add_message(role="system", content=build_instructions("Sunrise Clinic"))
    ctx.add_message(role="user", content=text)
    engine = inference.LLM(
        model=model,
        extra_kwargs={"temperature": 0.3, "max_completion_tokens": 250},
    )
    started = time.monotonic()
    ttft = None
    reply = ""
    try:
        async with engine.chat(chat_ctx=ctx) as stream:
            async for chunk in stream:
                if chunk.delta and chunk.delta.content:
                    if ttft is None:
                        ttft = time.monotonic() - started
                    reply += chunk.delta.content
    finally:
        await engine.aclose()
    return ttft, reply


async def main(models: list[str]) -> int:
    if not models:
        print(__doc__)
        return 1
    load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
    for model in models:
        print(f"\n=== {model}")
        ttfts: list[float] = []
        for round_number in range(ROUNDS):
            for turn in TURNS:
                try:
                    ttft, reply = await asyncio.wait_for(ask(model, turn), 45)
                except Exception as e:
                    print(f"  ERROR on {turn!r}: {type(e).__name__}: {e}")
                    continue
                if ttft is not None:
                    ttfts.append(ttft)
                if round_number == 0:
                    print(f"  user : {turn}\n  agent: {reply}")
        if ttfts:
            print(
                f"  time to first token: n={len(ttfts)} "
                f"median={statistics.median(ttfts):.2f}s "
                f"min={min(ttfts):.2f}s max={max(ttfts):.2f}s"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
