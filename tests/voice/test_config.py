import pytest

from voice_agent.config import DEFAULT_GREETING, ConfigError, load_settings

from .fakes import BASE_ENV, make_settings


def test_defaults_target_telugu() -> None:
    settings = make_settings()

    assert settings.stt.mode == "standard"
    assert settings.stt.model == "saaras:v4"
    assert settings.stt.task == "codemix"
    assert settings.stt.language == "te-IN"
    assert settings.tts.model == "bulbul:v3"
    assert settings.tts.language == "te-IN"
    assert settings.llm.provider == "livekit"
    assert settings.llm.model == "google/gemma-4-31b-it"
    assert settings.turn.detection == "vad"
    assert settings.greeting == DEFAULT_GREETING


def test_missing_sarvam_key_is_reported() -> None:
    with pytest.raises(ConfigError, match="SARVAM_API_KEY"):
        load_settings({})


def test_blank_values_fall_back_to_defaults() -> None:
    settings = make_settings(SARVAM_TTS_SPEAKER="  ", LLM_MODEL="", AGENT_GREETING="")

    assert settings.tts.speaker == "kavitha"
    assert settings.llm.model == "google/gemma-4-31b-it"
    assert settings.greeting == DEFAULT_GREETING


def test_voice_model_and_language_are_configurable() -> None:
    settings = make_settings(
        SARVAM_STT_MODE="realtime",
        SARVAM_STT_TASK="transcribe",
        SARVAM_STT_SAMPLE_RATE="8000",
        SARVAM_TTS_SPEAKER="Shubh",
        SARVAM_TTS_PACE="1.15",
        SARVAM_TTS_LANGUAGE="en-IN",
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="sk-test",
        LLM_MODEL="gpt-4.1",
        TURN_DETECTION="stt",
        AGENT_GREETING="Hello!",
    )

    assert settings.stt.mode == "realtime"
    assert settings.stt.task == "transcribe"
    assert settings.stt.sample_rate == 8000
    assert settings.tts.speaker == "shubh"
    assert settings.tts.pace == 1.15
    assert settings.tts.language == "en-IN"
    assert settings.llm.provider == "openai"
    assert settings.llm.model == "gpt-4.1"
    assert settings.turn.detection == "stt"
    assert settings.greeting == "Hello!"


def test_legacy_stt_mode_name_from_the_brief_is_accepted() -> None:
    assert make_settings(SARVAM_STT_MODE="legacy").stt.mode == "standard"


@pytest.mark.parametrize(
    ("mode", "given", "expected"),
    [
        ("standard", "auto", "unknown"),
        ("standard", "unknown", "unknown"),
        ("realtime", "unknown", "auto"),
        ("realtime", "auto", "auto"),
    ],
)
def test_language_detection_spelling_follows_the_stt_api(
    mode: str, given: str, expected: str
) -> None:
    settings = make_settings(SARVAM_STT_MODE=mode, SARVAM_STT_LANGUAGE=given)

    assert settings.stt.language == expected


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SARVAM_STT_MODE", "batch"),
        ("SARVAM_STT_TASK", "summarise"),
        ("SARVAM_STT_SAMPLE_RATE", "44100"),
        ("SARVAM_TTS_MODEL", "bulbul:v9"),
        ("SARVAM_TTS_SPEAKER", "anushka"),  # a bulbul:v2 voice, not valid on v3
        ("SARVAM_TTS_PACE", "5"),
        ("SARVAM_TTS_PACE", "fast"),
        ("SARVAM_TTS_SAMPLE_RATE", "11025"),
        ("SARVAM_TTS_MIN_BUFFER_SIZE", "10"),
        ("LLM_PROVIDER", "bedrock"),
        ("LLM_TEMPERATURE", "9"),
        ("TURN_DETECTION", "telepathy"),
        ("INTERRUPTION_MODE", "always"),
        ("NOISE_CANCELLATION", "maybe"),
        ("VOICE_LOG_LEVEL", "loud"),
    ],
)
def test_invalid_values_are_rejected_by_name(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        make_settings(**{name: value})


def test_endpointing_max_must_not_be_below_min() -> None:
    with pytest.raises(ConfigError, match="TURN_ENDPOINTING_MAX_DELAY"):
        make_settings(TURN_ENDPOINTING_MIN_DELAY="2", TURN_ENDPOINTING_MAX_DELAY="1")


def test_openai_provider_requires_a_key() -> None:
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        make_settings(LLM_PROVIDER="openai")


def test_openai_compatible_provider_lists_what_is_missing() -> None:
    with pytest.raises(ConfigError, match="LLM_MODEL, LLM_BASE_URL, LLM_API_KEY"):
        make_settings(LLM_PROVIDER="openai_compatible")


def test_describe_and_repr_never_expose_secrets() -> None:
    settings = load_settings(
        {
            **BASE_ENV,
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "sk-very-secret",
        }
    )

    assert "sk-very-secret" not in str(settings.describe())
    assert "sk-very-secret" not in repr(settings)
    assert "test-sarvam-key" not in repr(settings)


def test_every_setting_is_documented_in_env_example() -> None:
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "voice_agent" / "config.py").read_text(encoding="utf-8")
    documented = (root / ".env.example").read_text(encoding="utf-8")

    names = set(re.findall(r'(?:env|reader)\.\w+\(\s*"([A-Z][A-Z0-9_]+)"', source))

    assert len(names) > 30
    assert [n for n in sorted(names) if f"\n{n}=" not in documented] == []
