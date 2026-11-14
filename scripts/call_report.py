"""Summarise the timing events of one call, to see why speech sounded broken up.

    python scripts/call_report.py                 # most recent session
    python scripts/call_report.py --session NAME  # a specific room
    python scripts/call_report.py --list

Reads logs/call_events.jsonl, which holds timings only and never what was said.
A "pause inside a reply" is the agent leaving the speaking state and returning to
it with no caller turn in between: the caller hears the sentence stop and resume.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

DEFAULT_LOG = Path(__file__).resolve().parent.parent / "logs" / "call_events.jsonl"


def load(path: Path) -> dict[str, list[dict]]:
    sessions: dict[str, list[dict]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        sessions[row.get("session", "?")].append(row)
    return sessions


def summarise(rows: list[dict]) -> dict:
    start = rows[0]["ts"]
    agent_turns = [r for r in rows if r["event"] == "turn" and r["role"] == "assistant"]
    user_turns = [r for r in rows if r["event"] == "turn" and r["role"] == "user"]
    overlaps = [r for r in rows if r["event"] == "overlapping_speech"]
    false = [r for r in rows if r["event"] == "false_interruption"]

    # Pauses inside a reply: speaking -> (not speaking) -> speaking, with no
    # user turn and no new speech created in between.
    pauses: list[tuple[float, float]] = []
    left_at: float | None = None
    broke = False
    user_talk_while_agent = 0
    agent_speaking = False
    for row in rows:
        event = row["event"]
        if event == "agent_state":
            if row["old"] == "speaking" and row["new"] != "speaking":
                left_at, broke, agent_speaking = row["ts"], False, False
            elif row["new"] == "speaking":
                if left_at is not None and not broke:
                    pauses.append((left_at - start, row["ts"] - left_at))
                left_at, agent_speaking = None, True
        elif event in ("speech_created",) or (
            event == "turn" and row["role"] == "user"
        ):
            broke = True
        elif event == "user_state" and row["new"] == "speaking" and agent_speaking:
            user_talk_while_agent += 1

    return {
        "duration": rows[-1]["ts"] - start,
        "agent_turns": len(agent_turns),
        "agent_turns_interrupted": sum(1 for r in agent_turns if r.get("interrupted")),
        "user_turns": len(user_turns),
        "very_short_user_turns": sum(
            1 for r in user_turns if (r.get("chars") or 0) <= 3
        ),
        "caller_voice_detected_while_agent_spoke": user_talk_while_agent,
        "overlaps_judged_interruption": sum(
            1 for r in overlaps if r.get("is_interruption")
        ),
        "overlaps_judged_not_interruption": sum(
            1 for r in overlaps if not r.get("is_interruption")
        ),
        "false_interruptions_resumed": sum(1 for r in false if r.get("resumed")),
        "false_interruptions_not_resumed": sum(
            1 for r in false if not r.get("resumed")
        ),
        "pauses_inside_replies": pauses,
    }


def format_report(session: str, summary: dict) -> str:
    pauses = summary["pauses_inside_replies"]
    lines = [
        f"Session: {session}   length {summary['duration']:.0f} s",
        f"  Agent turns: {summary['agent_turns']}  "
        f"(cut off by the caller: {summary['agent_turns_interrupted']})",
        f"  Caller turns: {summary['user_turns']}  "
        f"(3 characters or fewer: {summary['very_short_user_turns']})",
        f"  Caller voice detected while the agent spoke: "
        f"{summary['caller_voice_detected_while_agent_spoke']}",
        f"  Overlaps judged a real interruption: {summary['overlaps_judged_interruption']}",
        f"  Overlaps judged not an interruption: "
        f"{summary['overlaps_judged_not_interruption']}",
        f"  False interruptions, speech resumed: {summary['false_interruptions_resumed']}",
        f"  False interruptions, speech dropped: "
        f"{summary['false_interruptions_not_resumed']}",
        f"  Pauses inside replies: {len(pauses)}"
        + (f", longest {max(p[1] for p in pauses):.2f} s" if pauses else ""),
    ]
    lines += [f"    at +{at:5.1f} s for {length:.2f} s" for at, length in pauses]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("log", nargs="?", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--session")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if not args.log.exists():
        raise SystemExit(f"No event log at {args.log}. Make a call first.")
    sessions = load(args.log)
    if args.list:
        for name, rows in sessions.items():
            print(f"{name}  {len(rows)} events")
        return
    name = args.session or list(sessions)[-1]
    if name not in sessions:
        raise SystemExit(f"No session named {name}. Use --list.")
    print(format_report(name, summarise(sessions[name])))


if __name__ == "__main__":
    main()
