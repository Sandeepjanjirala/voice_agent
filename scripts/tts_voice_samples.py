"""Speak the same lines in several Sarvam voices and measure each stream.

    python scripts/tts_voice_samples.py kavitha,neha,priya
    python scripts/tts_voice_samples.py kavitha --out voice-samples --text "ఒక వాక్యం"

Uses the worker's own TTS settings from .env.local, changing only the voice.
Writes one WAV per voice (git-ignored) for listening, and prints for every line:
time to first audio, audio length, the worst playback stall (audio arriving
slower than real time) and silent gaps inside the audio. Numbers cannot say
whether a voice sounds natural; that needs a person listening.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env.local")

from livekit.agents.utils import http_context  # noqa: E402

from voice_agent.config import ConfigError, load_settings  # noqa: E402
from voice_agent.pipeline import build_tts  # noqa: E402

SAMPLE_LINES = [
    "హలో అండి! నేను మీ AI assistant ని. మీకు ఏం help కావాలి?",
    "సరే అండి, రేపు morning పది గంటలకి appointment కావాలా? మా office వాళ్ళు call చేసి confirm చేస్తారు.",
    "Sure, I can help you in English. What would you like to know?",
    "అయ్యో, sorry అండి. ఆ details నా దగ్గర లేవు, మా వాళ్ళు call చేసి చెప్తారు.",
    "సరే అండి, thank you. ఉంటాను.",
]


def silent_gaps(pcm: np.ndarray, rate: int, min_gap: float = 0.25) -> list[float]:
    """Lengths of silences of at least `min_gap` seconds inside the speech."""
    frame = int(rate * 0.02)
    count = len(pcm) // frame
    if count == 0:
        return []
    blocks = pcm[: count * frame].astype(np.float64).reshape(count, frame)
    level = 20 * np.log10(np.maximum(np.sqrt((blocks**2).mean(axis=1)), 1) / 32768)
    voiced = level > -45
    spoken = np.flatnonzero(voiced)
    if len(spoken) == 0:
        return []
    gaps, run = [], 0
    for is_voiced in voiced[spoken[0] : spoken[-1] + 1]:
        if is_voiced:
            if run * 0.02 >= min_gap:
                gaps.append(round(run * 0.02, 2))
            run = 0
        else:
            run += 1
    return gaps


async def synthesise(tts, text: str):
    stream = tts.stream()
    started = time.perf_counter()
    # Word by word with a short delay, roughly as an LLM token stream arrives.
    for word in text.split(" "):
        stream.push_text(word + " ")
        await asyncio.sleep(0.03)
    stream.end_input()
    first, received, stall, rate, chunks = None, 0.0, 0.0, 0, []
    async for event in stream:
        now = time.perf_counter() - started
        first = now if first is None else first
        rate = event.frame.sample_rate
        chunks.append(np.frombuffer(event.frame.data, dtype=np.int16).copy())
        # Playback starts with the first audio; a positive value means it ran dry.
        stall = max(stall, (now - first) - received)
        received += event.frame.samples_per_channel / rate
    await stream.aclose()
    pcm = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    return first, received, stall, pcm, rate


async def run(voices: list[str], lines: list[str], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    async with http_context.open():
        for voice in voices:
            try:
                settings = load_settings({**os.environ, "SARVAM_TTS_SPEAKER": voice})
            except ConfigError as error:
                print(f"{voice}: skipped ({error})")
                continue
            tts = build_tts(settings.tts)
            firsts, audio, rate = [], [], 0
            for number, text in enumerate(lines, 1):
                try:
                    first, seconds, stall, pcm, rate = await synthesise(tts, text)
                except Exception as error:
                    print(f"{voice} line {number}: failed ({type(error).__name__})")
                    continue
                firsts.append(first)
                audio += [pcm, np.zeros(int(rate * 0.7), dtype=np.int16)]
                print(
                    f"{voice} line {number}: first audio {first:.2f} s, "
                    f"audio {seconds:.2f} s, worst stall {max(stall, 0):.2f} s, "
                    f"gaps {silent_gaps(pcm, rate) or 'none'}"
                )
            await tts.aclose()
            if not audio:
                continue
            path = out_dir / f"{voice}.wav"
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(rate)
                out.writeframes(np.concatenate(audio).tobytes())
            median = sorted(firsts)[len(firsts) // 2]
            print(f"{voice}: median first audio {median:.2f} s -> {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("voices", help="comma-separated voice names")
    parser.add_argument("--out", type=Path, default=ROOT / "voice-samples")
    parser.add_argument("--text", action="append", help="line to speak; repeatable")
    args = parser.parse_args()
    asyncio.run(run(args.voices.split(","), args.text or SAMPLE_LINES, args.out))


if __name__ == "__main__":
    main()
