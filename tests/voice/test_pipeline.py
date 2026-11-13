"""Builds the real plugin objects (no network) to catch SDK API drift early."""

import pytest
from livekit.agents import inference, stt
from livekit.plugins import openai, sarvam

from voice_agent.pipeline import (
    build_llm,
    build_session,
    build_stt,
    build_tts,
    build_turn_handling,
)

from .fakes import make_settings


@pytest.fixture(autouse=True)
def _provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    # The plugins read their keys from the process environment at construction.
    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key")
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")


async def test_standard_stt_is_telugu_codemix_saaras_v4() -> None:
    engine = build_stt(make_settings().stt)

    assert isinstance(engine, sarvam.STT)
    assert engine.model == "saaras:v4"
    assert engine._opts.language == "te-IN"
    assert engine._opts.mode == "codemix"
    assert engine.capabilities.streaming


async def test_realtime_stt_falls_back_to_reconnecting_standard_stt() -> None:
    engine = build_stt(make_settings(SARVAM_STT_MODE="realtime").stt)

    assert isinstance(engine, stt.FallbackAdapter)
    primary, fallback = engine._stt_instances
    assert isinstance(primary, sarvam.STTRealtime)
    assert isinstance(fallback, sarvam.STT)


async def test_realtime_stt_fallback_can_be_disabled() -> None:
    settings = make_settings(
        SARVAM_STT_MODE="realtime", SARVAM_STT_REALTIME_FALLBACK="false"
    )

    assert isinstance(build_stt(settings.stt), sarvam.STTRealtime)


async def test_realtime_auto_language_maps_to_fallback_spelling() -> None:
    settings = make_settings(SARVAM_STT_MODE="realtime", SARVAM_STT_LANGUAGE="auto")

    engine = build_stt(settings.stt)

    assert isinstance(engine, stt.FallbackAdapter)
    primary, fallback = engine._stt_instances
    assert primary._opts.language == "auto"
    assert fallback._opts.language == "unknown"


async def test_tts_uses_configured_telugu_voice() -> None:
    settings = make_settings(SARVAM_TTS_SPEAKER="pooja", SARVAM_TTS_PACE="1.1")

    engine = build_tts(settings.tts)

    assert isinstance(engine, sarvam.TTS)
    assert engine.model == "bulbul:v3"
    assert engine._opts.target_language_code == "te-IN"
    assert engine._opts.speaker == "pooja"
    assert engine._opts.pace == 1.1
    assert engine.sample_rate == 22050


async def test_llm_defaults_to_livekit_inference() -> None:
    engine = build_llm(make_settings().llm)

    assert isinstance(engine, inference.LLM)
    assert engine.model == "google/gemma-4-31b-it"


async def test_llm_openai_provider() -> None:
    settings = make_settings(LLM_PROVIDER="openai", OPENAI_API_KEY="sk-test")

    engine = build_llm(settings.llm)

    assert isinstance(engine, openai.LLM)
    assert engine.model == "gpt-4.1-mini"


async def test_llm_openai_compatible_provider_uses_base_url() -> None:
    settings = make_settings(
        LLM_PROVIDER="openai_compatible",
        LLM_MODEL="some-model",
        LLM_BASE_URL="https://llm.example.test/v1",
        LLM_API_KEY="key",
    )

    engine = build_llm(settings.llm)

    assert isinstance(engine, openai.LLM)
    assert engine.model == "some-model"
    assert str(engine._client.base_url).startswith("https://llm.example.test/v1")


def test_turn_handling_defaults_allow_barge_in() -> None:
    options = build_turn_handling(make_settings().turn)

    assert options["turn_detection"] == "vad"
    assert options["endpointing"] == {"min_delay": 0.5, "max_delay": 3.0}
    assert options["interruption"]["enabled"] is True
    assert options["interruption"]["resume_false_interruption"] is True
    # "auto" must leave the mode unset so the SDK picks adaptive vs VAD itself.
    assert "mode" not in options["interruption"]
    assert options["preemptive_generation"] == {"enabled": True}


def test_turn_handling_is_configurable() -> None:
    settings = make_settings(
        TURN_DETECTION="stt",
        TURN_ENDPOINTING_MIN_DELAY="0.8",
        TURN_ENDPOINTING_MAX_DELAY="4",
        INTERRUPTION_MODE="vad",
        INTERRUPTION_MIN_DURATION="0.3",
        PREEMPTIVE_GENERATION="false",
    )

    options = build_turn_handling(settings.turn)

    assert options["turn_detection"] == "stt"
    assert options["endpointing"] == {"min_delay": 0.8, "max_delay": 4.0}
    assert options["interruption"]["mode"] == "vad"
    assert options["interruption"]["min_duration"] == 0.3
    assert options["preemptive_generation"] == {"enabled": False}


async def test_livekit_turn_detector_is_opt_in() -> None:
    options = build_turn_handling(make_settings(TURN_DETECTION="livekit").turn)

    assert isinstance(options["turn_detection"], inference.TurnDetector)


async def test_full_session_builds_with_default_settings() -> None:
    session = build_session(make_settings())

    assert isinstance(session.stt, sarvam.STT)
    assert isinstance(session.tts, sarvam.TTS)
    assert isinstance(session.llm, inference.LLM)
