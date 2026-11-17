"""The shape of an employee's configuration, and the only way to build one.

Everything that reaches the database or a call passes through `normalize`,
whether it came from the LLM, a template or the editor, so sizes and types are
always known. Steps are kept short on purpose: they end up in the voice
worker's per-call prompt, which has a small byte budget.
"""

import re
from typing import Any

SCHEMA = 1
MAX_STEPS = 6
MAX_QUESTIONS = 8
MAX_OPEN_QUESTIONS = 8
LIMITS = {
    "agent_name": 40,
    "role": 80,
    "greeting": 250,
    "purpose": 300,
    "limits": 300,
    "ending": 200,
    "step_name": 40,
    "step_guidance": 160,
    "question": 80,
    "open_question": 160,
}
DEFAULT_STRICTNESS = 3


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

    open_questions = [
        text
        for item in _items(raw.get("open_questions"))
        if (text := _text(item, LIMITS["open_question"]))
    ][:MAX_OPEN_QUESTIONS]

    try:
        strictness = int(raw.get("strictness", DEFAULT_STRICTNESS))
    except (TypeError, ValueError):
        strictness = DEFAULT_STRICTNESS

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
    }
