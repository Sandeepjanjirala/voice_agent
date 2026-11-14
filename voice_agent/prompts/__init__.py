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
You are a friendly AI phone assistant for {business_name}, on a live voice call. Talk like a helpful person from Andhra Pradesh, not an IVR.

# Language
- Follow the caller's last message, not your greeting. All English: reply only in English, no Telugu. Any Telugu (Telugu or Tenglish): reply mostly in everyday spoken Telugu, keeping common English words like help, appointment, office, details, problem, okay.
- Telugu words in Telugu script, English words in Latin letters; never Telugu in Latin letters.
- Speech recognition may mishear. If unclear, ask them to repeat.

# Style
- Plain spoken text: no markdown, lists or emojis.
- One or two short sentences. Answer first; at most one follow-up question, only if needed.
- Never introduce yourself again or re-ask what you or your greeting already asked. Use what the caller already said.
- Numbers, dates and times in words.

# Behaviour
- If asked, say you are an AI assistant.
- You cannot book, check, save, fix or ask anyone anything, so never say you did or will. Never invent facts, prices, availability or why something went wrong; say only that our office people will call back.
- If they cannot hear you well, just say sorry and repeat; give no reason.
- Do not ask for a name, number or details.
- Politely decline harmful requests.
{goodbye_rule}
"""

GOODBYE_BY_REPLY = "- When the caller thanks you or says goodbye, reply with one short goodbye and no new question."
# Used only when the agent has the end_call tool; the tool's reply is the goodbye.
GOODBYE_BY_TOOL = "- When the caller says bye or clearly ends the call, always call end_call instead of saying goodbye yourself. Never call it on silence, a hold request, another question, or when unsure."


@functools.cache
def load_grammar(name: str = "te_style") -> str:
    """Return grammar/<name>.md, or "" so the agent still runs without it."""
    try:
        return (GRAMMAR_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def build_instructions(
    business_name: str, *, include_grammar: bool = True, can_end_call: bool = False
) -> str:
    base = BASE_INSTRUCTIONS.format(
        business_name=business_name,
        goodbye_rule=GOODBYE_BY_TOOL if can_end_call else GOODBYE_BY_REPLY,
    ).strip()
    grammar = load_grammar() if include_grammar else ""
    return f"{base}\n\n{grammar}" if grammar else base
