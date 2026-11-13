"""Summarise per-turn latency recorded by the voice worker.

    uv run python scripts/latency_report.py [logs/turn_metrics.jsonl]

Prints count, median, p90 and max for each latency stage. `e2e_latency` is the
gap a caller feels: end of their speech to the start of the agent's reply.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_LOG = Path(__file__).resolve().parent.parent / "logs" / "turn_metrics.jsonl"

STAGES = (
    ("end_of_turn_delay", "End-of-turn detection"),
    ("transcription_delay", "STT transcription delay"),
    ("llm_node_ttft", "LLM time to first token"),
    ("tts_node_ttfb", "TTS time to first byte"),
    ("e2e_latency", "Perceived turn latency (e2e)"),
)


def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a call that died mid-write must not hide the rest
    return rows


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def summarise(rows: list[dict]) -> dict[str, dict[str, float]]:
    samples: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for key, _ in STAGES:
            if isinstance(row.get(key), int | float):
                samples[key].append(float(row[key]))
    return {
        key: {
            "count": len(values),
            "p50": statistics.median(values),
            "p90": percentile(values, 0.9),
            "max": max(values),
        }
        for key, values in samples.items()
    }


def format_report(rows: list[dict]) -> str:
    summary = summarise(rows)
    sessions = {row.get("session") for row in rows}
    assistant_turns = [r for r in rows if r.get("role") == "assistant"]
    interrupted = sum(1 for r in assistant_turns if r.get("interrupted"))

    lines = [
        f"Sessions: {len(sessions)}   Turns: {len(rows)}   "
        f"Agent turns interrupted: {interrupted}/{len(assistant_turns)}",
        "",
        f"{'Stage':<32}{'n':>5}{'p50 (s)':>10}{'p90 (s)':>10}{'max (s)':>10}",
    ]
    for key, label in STAGES:
        if key not in summary:
            lines.append(f"{label:<32}{0:>5}{'-':>10}{'-':>10}{'-':>10}")
            continue
        s = summary[key]
        lines.append(
            f"{label:<32}{s['count']:>5}{s['p50']:>10.3f}{s['p90']:>10.3f}{s['max']:>10.3f}"
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_LOG
    if not path.exists():
        print(f"No metrics log at {path}. Talk to the agent first.")
        return 1
    rows = load_rows(path)
    if not rows:
        print(f"{path} has no turns yet.")
        return 1
    print(format_report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
