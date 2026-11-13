from voice_agent.config import DEFAULT_FALLBACK_MESSAGE, DEFAULT_GREETING
from voice_agent.prompts import (
    PROMPT_BUDGET_BYTES,
    build_instructions,
    load_grammar,
)


def _has_telugu(text: str) -> bool:
    return any("ఀ" <= ch <= "౿" for ch in text)


def test_prompt_stays_within_latency_budget() -> None:
    prompt = build_instructions("A Reasonably Long Business Name Pvt Ltd")

    assert len(prompt.encode("utf-8")) <= PROMPT_BUDGET_BYTES


def test_prompt_covers_all_three_language_styles() -> None:
    prompt = build_instructions("Acme")

    for expected in ("Telugu", "English", "Tenglish", "Telugu script"):
        assert expected in prompt


def test_prompt_requires_ai_disclosure_and_forbids_invention() -> None:
    prompt = build_instructions("Acme")

    assert "AI assistant" in prompt
    assert "Never invent" in prompt


def test_prompt_names_the_business() -> None:
    assert "Sunrise Clinic" in build_instructions("Sunrise Clinic")


def test_grammar_sheet_is_loaded_and_optional() -> None:
    grammar = load_grammar()

    assert _has_telugu(grammar)
    assert grammar in build_instructions("Acme")
    assert grammar not in build_instructions("Acme", include_grammar=False)


def test_missing_grammar_sheet_does_not_break_the_prompt() -> None:
    assert load_grammar("does_not_exist") == ""


def test_default_spoken_lines_are_in_telugu_script() -> None:
    # Romanised Telugu sent to a te-IN voice is pronounced badly.
    assert _has_telugu(DEFAULT_GREETING)
    assert _has_telugu(DEFAULT_FALLBACK_MESSAGE)
    assert "AI" in DEFAULT_GREETING
