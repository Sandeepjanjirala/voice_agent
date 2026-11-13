"""System prompt assembly for the Telugu voice agent.

The system prompt is re-sent to the LLM on every turn, so its size directly
costs time-to-first-token. Keep the whole prompt under PROMPT_BUDGET_BYTES and
move reference data into tools (Phase 4) instead of pasting it here.
"""

from __future__ import annotations

import functools
from pathlib import Path

GRAMMAR_DIR = Path(__file__).resolve().parent.parent / "grammar"

PROMPT_BUDGET_BYTES = 2048

BASE_INSTRUCTIONS = """\
You are a polite AI phone assistant for {business_name}. You are on a live voice call.

# Language
- Reply in the language the caller just used: Telugu, English, or mixed Telugu-English (Tenglish). If unsure, use Telugu.
- Write Telugu words in Telugu script and English words in Latin letters. Never write Telugu in Latin letters.
- The caller's words come from speech recognition and may contain errors. If something is unclear, ask them to repeat instead of guessing.

# Speaking style
- Plain spoken text only: no markdown, lists, emojis or symbols.
- One or two short sentences per reply, with normal punctuation. Ask one question at a time.
- Spell out numbers, dates and times in words.

# Behaviour
- If asked, say that you are an AI assistant.
- You cannot book, check, look up or change anything yet, so never say you will. Never invent facts, prices, names or availability; say a colleague will confirm.
- Do not ask for the caller's name, number or other details; you cannot save them yet.
- Decline harmful or out-of-scope requests politely.
- When the caller thanks you or says goodbye, reply with one short goodbye and no new question.
"""


@functools.cache
def load_grammar(name: str = "te_style") -> str:
    """Return grammar/<name>.md, or "" so the agent still runs without it."""
    try:
        return (GRAMMAR_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def build_instructions(business_name: str, *, include_grammar: bool = True) -> str:
    base = BASE_INSTRUCTIONS.format(business_name=business_name).strip()
    grammar = load_grammar() if include_grammar else ""
    return f"{base}\n\n{grammar}" if grammar else base
