"""The conversation of a call, made reviewable.

What the voice worker reports is untrusted: every turn is cleaned here before it
is stored. The same turns are then written out for the analysis (with times,
cut-offs and the tools the AI used), and checked for problems that can be
measured rather than guessed: the person speaking over the AI, a long wait for
a reply, the AI repeating itself word for word, a last question left without a
reply, speech recognition that was unsure, tools that failed.

Problems the AI analysis reports are kept only when the words it quotes as
evidence are really in the transcript.
"""

import itertools
import re

MAX_TURNS = 160
TURN_CHARS = 600
# Thresholds for the measured checks.
SLOW_REPLY_SECONDS = 3.0
WAIT_SECONDS = 6.0
UNSURE_CONFIDENCE = 0.5
REPEAT_MIN_CHARS = 15
QUOTE_MIN_CHARS = 4

TOOL_NAME = re.compile(r"[^a-z0-9_]")


def _seconds(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(float(value), 1) if 0 <= value <= 7200 else None


def clean_turns(turns) -> list[dict]:
    """The worker's turns as they may be stored: bounded and typed."""
    if not isinstance(turns, list):
        return []
    clean = []
    for turn in turns[:MAX_TURNS]:
        if not isinstance(turn, dict):
            continue
        at = _seconds(turn.get("t"))
        if turn.get("role") == "tool":
            name = TOOL_NAME.sub("", str(turn.get("name", "")).lower())[:40]
            if name:
                entry = {
                    "role": "tool",
                    "name": name,
                    "ok": turn.get("ok") is not False,
                }
                if at is not None:
                    entry["t"] = at
                clean.append(entry)
            continue
        text = " ".join(str(turn.get("text", "")).split())[:TURN_CHARS]
        if not text:
            continue
        entry = {
            "role": "assistant" if turn.get("role") == "assistant" else "user",
            "text": text,
        }
        if at is not None:
            entry["t"] = at
        if turn.get("interrupted") is True and entry["role"] == "assistant":
            entry["interrupted"] = True
        confidence = turn.get("confidence")
        if (
            entry["role"] == "user"
            and isinstance(confidence, int | float)
            and not isinstance(confidence, bool)
            and 0 <= confidence <= 1
        ):
            entry["confidence"] = round(float(confidence), 2)
        clean.append(entry)
    return clean


def clock(seconds) -> str:
    """0:07, 12:45: minutes and seconds from the start of the conversation."""
    if seconds is None:
        return ""
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def spoken(turns: list[dict]) -> list[dict]:
    return [turn for turn in turns if turn.get("role") in ("user", "assistant")]


def for_analysis(turns: list[dict], limit: int) -> str:
    """The conversation as the analysis reads it."""
    lines = []
    for turn in turns:
        at = f"[{clock(turn.get('t'))}] " if turn.get("t") is not None else ""
        if turn["role"] == "tool":
            state = "worked" if turn.get("ok") else "failed"
            lines.append(f"{at}(The AI employee used the {turn['name']} tool: {state})")
            continue
        if turn["role"] == "assistant":
            who = (
                "AI employee (cut off by the person)"
                if turn.get("interrupted")
                else "AI employee"
            )
        else:
            unsure = turn.get("confidence") is not None and (
                turn["confidence"] < UNSURE_CONFIDENCE
            )
            who = "Person (speech recognition unsure)" if unsure else "Person"
        lines.append(f"{at}{who}: {turn['text']}")
    return "\n".join(lines)[:limit]


def _plain(text: str) -> str:
    return " ".join(str(text).lower().split()).strip(" .,!?\"'“”‘’")


def quote_in(quote, turns: list[dict]) -> str:
    """The quote, if those words are really in the conversation; otherwise ""."""
    if not isinstance(quote, str):
        return ""
    wanted = _plain(quote)
    if len(wanted) < QUOTE_MIN_CHARS:
        return ""
    for turn in spoken(turns):
        if wanted in _plain(turn["text"]):
            return " ".join(quote.split())[:200]
    return ""


def measured_issues(turns: list[dict], record) -> list[dict]:
    """Problems shown by the call's own data, with their evidence."""
    issues = []

    def add(kind, detail, quote="", at=None):
        issue = {"kind": kind, "detail": detail, "quote": quote, "source": "data"}
        if at is not None:
            issue["at"] = clock(at)
        issues.append(issue)

    talk = spoken(turns)
    cut_off = [turn for turn in talk if turn.get("interrupted")]
    if cut_off:
        add(
            "interrupted",
            f"The person spoke over the AI employee {len(cut_off)} "
            f"time{'s' if len(cut_off) > 1 else ''}.",
            cut_off[0]["text"][:200],
            cut_off[0].get("t"),
        )
    elif record.interruptions:
        add(
            "interrupted",
            f"The person spoke over the AI employee {record.interruptions} "
            f"time{'s' if record.interruptions > 1 else ''}.",
        )

    seen: dict[str, list[dict]] = {}
    for turn in talk:
        if (
            turn["role"] == "assistant"
            and len(_plain(turn["text"])) >= REPEAT_MIN_CHARS
        ):
            seen.setdefault(_plain(turn["text"]), []).append(turn)
    for repeats in seen.values():
        if len(repeats) > 1:
            add(
                "repeated",
                f"The AI employee said the same thing {len(repeats)} times.",
                repeats[0]["text"][:200],
                repeats[1].get("t"),
            )

    # A person's turn is timed when their words were taken, and the AI's reply
    # when it started speaking (LiveKit's own timings), so the gap between the
    # two is how long the person waited in silence. The other way round the
    # gap includes both of them talking, so it says nothing about silence.
    slow = 0
    for before, after in itertools.pairwise(talk):
        if before["role"] != "user" or after["role"] != "assistant":
            continue
        if before.get("t") is None or after.get("t") is None:
            continue
        gap = after["t"] - before["t"]
        if gap >= WAIT_SECONDS:
            slow += 1
            add(
                "silence",
                f"The person waited about {int(gap)} seconds for a reply.",
                before["text"][:200],
                before["t"],
            )

    unsure = [
        turn
        for turn in talk
        if turn["role"] == "user"
        and turn.get("confidence") is not None
        and turn["confidence"] < UNSURE_CONFIDENCE
    ]
    if unsure:
        add(
            "unclear_speech",
            f"Speech recognition was unsure about {len(unsure)} of the person's "
            f"turn{'s' if len(unsure) > 1 else ''}; the words shown may be wrong.",
            unsure[0]["text"][:200],
            unsure[0].get("t"),
        )

    if (
        talk
        and talk[-1]["role"] == "user"
        and record.end_reason
        in (
            record.EndReason.AGENT,
            record.EndReason.TIME_LIMIT,
            record.EndReason.ERROR,
            record.EndReason.SYSTEM,
        )
    ):
        add(
            "unanswered",
            "The person's last words got no reply before the call ended.",
            talk[-1]["text"][:200],
            talk[-1].get("t"),
        )

    for turn in turns:
        if turn["role"] == "tool" and not turn.get("ok"):
            add("tool_failed", f"The {turn['name']} tool failed.", at=turn.get("t"))

    if (
        not slow
        and record.e2e_latency_max is not None
        and record.e2e_latency_max > SLOW_REPLY_SECONDS
    ):
        add(
            "slow_reply",
            f"The slowest reply took {record.e2e_latency_max:.1f} seconds.",
        )
    if record.end_reason == record.EndReason.ERROR:
        add("error", "A technical error ended the call.")
    return issues
