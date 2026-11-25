"""The shape of an employee's configuration, and the only way to build one.

Everything that reaches the database or a call passes through `normalize`,
whether it came from the LLM, a template or the editor, so sizes and types are
always known. Steps are kept short on purpose: they end up in the voice
worker's per-call prompt, which has a small byte budget.
"""

import re
from typing import Any

from .voices import DEFAULT_PACE, PACE_CHOICES, VOICE_NAMES

SCHEMA = 2
MAX_STEPS = 12
MAX_QUESTIONS = 12
MAX_OPEN_QUESTIONS = 8
# "When this happens, do that" rules, and the results a call can end with.
MAX_SITUATIONS = 10
MAX_OUTCOMES = 8
# Characters per field. The real limit is the byte budget of the instructions
# together (EMPLOYEE_INSTRUCTION_BYTES), checked when publishing and shown live
# in the editor; these only stop a single field from being unreasonable.
LIMITS = {
    "agent_name": 60,
    "role": 120,
    "greeting": 400,
    "purpose": 1200,
    "limits": 1500,
    "ending": 600,
    "step_name": 80,
    "step_guidance": 600,
    "question": 120,
    "open_question": 200,
    "success": 600,
    "escalation": 600,
    "situation_when": 200,
    "situation_then": 400,
    "outcome": 60,
    "information": 4000,
}
DEFAULT_STRICTNESS = 3
PACES = tuple(float(value) for value, _ in PACE_CHOICES)

# What an employee may have the system do after a call, each switched on per
# employee. Every action is carried out by Django and logged; an action that is
# not listed here cannot be claimed by an employee.
ACTIONS = {
    "create_follow_up": (
        "Create a follow-up task",
        "When the person asks to be called back, add a follow-up to the lead.",
    ),
    "update_lead_stage": (
        "Move the lead to a new stage",
        "Set the lead to Interested, Follow up or Closed from the call's outcome.",
    ),
}


def _text(value: Any, limit: int) -> str:
    """One line of plain text, never longer than `limit`."""
    if not isinstance(value, str | int | float) or isinstance(value, bool):
        return ""
    return " ".join(str(value).split())[:limit].strip()


def _key(label: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:30] or "answer"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    taken.add(key)
    return key


def _items(value: Any) -> list:
    return value if isinstance(value, list) else []


def _lines(value: Any, limit: int) -> str:
    """Plain text that keeps its line breaks (one fact per line), never
    longer than `limit`."""
    if not isinstance(value, str):
        return ""
    lines = (" ".join(line.split()) for line in value.splitlines())
    return "\n".join(line for line in lines if line)[:limit].strip()


def normalize(raw: Any) -> dict[str, Any]:
    """A valid configuration built from untrusted input. Unknown keys are dropped."""
    raw = raw if isinstance(raw, dict) else {}

    steps = []
    for item in _items(raw.get("steps")):
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"), LIMITS["step_name"])
        guidance = _text(item.get("guidance"), LIMITS["step_guidance"])
        if name or guidance:
            steps.append({"name": name or "Step", "guidance": guidance})
    steps = steps[:MAX_STEPS]

    questions, taken = [], set()
    for item in _items(raw.get("questions")):
        label = _text(
            item.get("label") if isinstance(item, dict) else item, LIMITS["question"]
        )
        if label:
            questions.append({"key": _key(label, taken), "label": label})
    questions = questions[:MAX_QUESTIONS]

    situations = []
    for item in _items(raw.get("situations")):
        if not isinstance(item, dict):
            continue
        when = _text(item.get("when"), LIMITS["situation_when"])
        then = _text(item.get("then"), LIMITS["situation_then"])
        if when and then:
            situations.append({"when": when, "then": then})
    situations = situations[:MAX_SITUATIONS]

    outcomes = []
    for item in _items(raw.get("outcomes")):
        label = _text(item, LIMITS["outcome"])
        if label and label.lower() not in (o.lower() for o in outcomes):
            outcomes.append(label)
    outcomes = outcomes[:MAX_OUTCOMES]

    open_questions = [
        text
        for item in _items(raw.get("open_questions"))
        if (text := _text(item, LIMITS["open_question"]))
    ][:MAX_OPEN_QUESTIONS]

    try:
        strictness = int(raw.get("strictness", DEFAULT_STRICTNESS))
    except (TypeError, ValueError):
        strictness = DEFAULT_STRICTNESS

    voice = raw.get("voice") if isinstance(raw.get("voice"), dict) else {}
    speaker = str(voice.get("speaker") or "").strip().lower()
    try:
        pace = float(voice.get("pace", DEFAULT_PACE))
    except (TypeError, ValueError):
        pace = DEFAULT_PACE
    actions = []
    for action in _items(raw.get("actions")):
        if not isinstance(action, str) or action in actions:
            continue
        # Built-in actions only; anything else (such as an old "custom:" key)
        # is dropped.
        if action in ACTIONS:
            actions.append(action)

    return {
        "schema": SCHEMA,
        "agent_name": _text(raw.get("agent_name"), LIMITS["agent_name"]),
        "role": _text(raw.get("role"), LIMITS["role"]),
        "greeting": _text(raw.get("greeting"), LIMITS["greeting"]),
        "purpose": _text(raw.get("purpose"), LIMITS["purpose"]),
        "steps": steps,
        "questions": questions,
        "limits": _text(raw.get("limits"), LIMITS["limits"]),
        "ending": _text(raw.get("ending"), LIMITS["ending"]),
        "strictness": min(max(strictness, 1), 5),
        "open_questions": open_questions,
        "voice": {
            "speaker": speaker if speaker in VOICE_NAMES else "",
            "pace": pace if pace in PACES else DEFAULT_PACE,
        },
        "actions": actions,
        # The rest of the job, all optional. What a good call achieves, how to
        # handle particular situations and when to hand over reach the call's
        # prompt; the possible results only the analysis after the call; the
        # information it may give is looked up by the employee when asked.
        "success": _text(raw.get("success"), LIMITS["success"]),
        "situations": situations,
        "escalation": _text(raw.get("escalation"), LIMITS["escalation"]),
        "outcomes": outcomes,
        "information": _lines(raw.get("information"), LIMITS["information"]),
    }
