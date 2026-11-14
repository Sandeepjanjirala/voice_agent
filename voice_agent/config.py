"""Voice worker configuration: one place that maps environment -> typed settings.

Everything an operator may want to change (models, voice, language, turn-taking)
is an environment variable documented in `.env.example`. `load_settings` takes a
mapping so tests can pass an explicit environment instead of the process one.

Value sets below were checked against livekit-plugins-sarvam 1.8.10; re-check
them when the plugin is upgraded.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from livekit.plugins.sarvam.models import MODEL_SPEAKER_COMPATIBILITY

PROJECT_ROOT = Path(__file__).resolve().parent.parent

STT_MODES = ("standard", "realtime")
STT_TASKS = ("transcribe", "translate", "verbatim", "translit", "codemix")
STT_MODELS = ("saaras:v4", "saaras:v3")
STT_SAMPLE_RATES = (8000, 16000)
TTS_SAMPLE_RATES = (8000, 16000, 22050, 24000, 32000, 44100, 48000)
LLM_PROVIDERS = ("livekit", "openai", "openai_compatible")
TURN_DETECTION_MODES = ("vad", "stt", "livekit")
INTERRUPTION_MODES = ("auto", "adaptive", "vad")

# LiveKit Inference retires models on a schedule (the openai/gpt-4.1 family goes
# on 14 January 2027). Check https://docs.livekit.io/agents/models/llm/ before
# changing this, and compare candidates with scripts/llm_bakeoff.py.
DEFAULT_LLM_MODELS = {
    "livekit": "google/gemma-4-31b-it",
    "openai": "gpt-4.1-mini",
}

DEFAULT_GREETING = "హలో అండి! నేను మీ AI assistant ని. మీకు ఏం help కావాలి?"
DEFAULT_FALLBACK_MESSAGE = "క్షమించండి, ప్రస్తుతం చిన్న సాంకేతిక సమస్య ఉంది. దయచేసి మళ్ళీ చెప్పగలరా?"


class ConfigError(ValueError):
    """Raised when the environment holds a missing or invalid setting."""


@dataclass(frozen=True)
class STTSettings:
    mode: str  # "standard" (saaras:v4/v3 streaming) or "realtime" (saaras:v3-realtime)
    model: str  # only used in standard mode; realtime is fixed by Sarvam
    task: str
    language: str
    sample_rate: int
    prompt: str | None
    high_vad_sensitivity: bool | None
    realtime_fallback: bool
    vad_min_silence_ms: int | None  # realtime mode only


@dataclass(frozen=True)
class TTSSettings:
    model: str
    language: str
    speaker: str
    pace: float
    temperature: float
    sample_rate: int
    min_buffer_size: int
    max_chunk_length: int
    dict_id: str | None


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    temperature: float
    max_tokens: int
    base_url: str | None
    # Never logged; see VoiceSettings.describe().
    api_key: str | None = field(repr=False)


@dataclass(frozen=True)
class TurnSettings:
    detection: str
    endpointing_min_delay: float
    endpointing_max_delay: float
    interruption_mode: str
    interruption_min_duration: float
    resume_false_interruption: bool
    preemptive_generation: bool


@dataclass(frozen=True)
class VoiceSettings:
    agent_name: str
    business_name: str
    greeting: str
    fallback_message: str
    noise_cancellation: bool
    # Off by default: with it off the agent has no tools and cannot hang up.
    can_end_call: bool
    # The only number the worker will dial. None disables outbound calls.
    outbound_allowed_number: str | None = field(repr=False)
    log_level: str
    log_transcripts: bool
    metrics_log_path: Path | None
    stt: STTSettings
    tts: TTSSettings
    llm: LLMSettings
    turn: TurnSettings

    def describe(self) -> dict[str, object]:
        """Selected models and tuning, safe to log (no secrets, no prompts)."""
        return {
            "agent_name": self.agent_name,
            "stt_mode": self.stt.mode,
            "stt_model": "saaras:v3-realtime"
            if self.stt.mode == "realtime"
            else self.stt.model,
            "stt_task": self.stt.task,
            "stt_language": self.stt.language,
            "stt_sample_rate": self.stt.sample_rate,
            "tts_model": self.tts.model,
            "tts_language": self.tts.language,
            "tts_speaker": self.tts.speaker,
            "tts_pace": self.tts.pace,
            "tts_sample_rate": self.tts.sample_rate,
            "llm_provider": self.llm.provider,
            "llm_model": self.llm.model,
            "turn_detection": self.turn.detection,
            "endpointing_min_delay": self.turn.endpointing_min_delay,
            "endpointing_max_delay": self.turn.endpointing_max_delay,
            "interruption_mode": self.turn.interruption_mode,
            "noise_cancellation": self.noise_cancellation,
            "can_end_call": self.can_end_call,
        }


class _Env:
    """Typed readers over an environment mapping; blank values count as unset."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self._env = env

    def str(self, name: str, default: str | None = None) -> str | None:
        value = self._env.get(name, "").strip()
        return value or default

    def choice(self, name: str, default: str, allowed: tuple[str, ...]) -> str:
        value = (self.str(name, default) or default).lower()
        if value not in allowed:
            raise ConfigError(f"{name}={value!r} must be one of: {', '.join(allowed)}")
        return value

    def bool(self, name: str, default: bool) -> bool:
        value = self.str(name)
        if value is None:
            return default
        if value.lower() in ("1", "true", "yes", "on"):
            return True
        if value.lower() in ("0", "false", "no", "off"):
            return False
        raise ConfigError(f"{name}={value!r} must be true or false")

    def optional_bool(self, name: str) -> bool | None:
        return None if self.str(name) is None else self.bool(name, False)

    def float(self, name: str, default: float, low: float, high: float) -> float:
        raw = self.str(name)
        try:
            value = default if raw is None else float(raw)
        except ValueError:
            raise ConfigError(f"{name}={raw!r} must be a number") from None
        if not low <= value <= high:
            raise ConfigError(f"{name}={value} must be between {low} and {high}")
        return value

    def int(self, name: str, default: int, low: int, high: int) -> int:
        raw = self.str(name)
        try:
            value = default if raw is None else int(raw)
        except ValueError:
            raise ConfigError(f"{name}={raw!r} must be a whole number") from None
        if not low <= value <= high:
            raise ConfigError(f"{name}={value} must be between {low} and {high}")
        return value

    def optional_int(self, name: str, low: int, high: int) -> int | None:
        return None if self.str(name) is None else self.int(name, low, low, high)

    def int_choice(self, name: str, default: int, allowed: tuple[int, ...]) -> int:
        value = self.int(name, default, min(allowed), max(allowed))
        if value not in allowed:
            options = ", ".join(str(v) for v in allowed)
            raise ConfigError(f"{name}={value} must be one of: {options}")
        return value


def _stt_settings(env: _Env) -> STTSettings:
    # The brief called the non-realtime API "legacy"; it is still a streaming
    # WebSocket API and the only one with saaras:v4, so we call it "standard".
    mode = (env.str("SARVAM_STT_MODE", "standard") or "standard").lower()
    if mode == "legacy":
        mode = "standard"
    if mode not in STT_MODES:
        raise ConfigError(
            f"SARVAM_STT_MODE={mode!r} must be one of: {', '.join(STT_MODES)}"
        )

    # Each API spells "detect the language" differently; accept either spelling.
    language = env.str("SARVAM_STT_LANGUAGE", "te-IN") or "te-IN"
    if language.lower() in ("auto", "unknown"):
        language = "auto" if mode == "realtime" else "unknown"

    return STTSettings(
        mode=mode,
        model=env.choice("SARVAM_STT_MODEL", "saaras:v4", STT_MODELS),
        task=env.choice("SARVAM_STT_TASK", "codemix", STT_TASKS),
        language=language,
        sample_rate=env.int_choice("SARVAM_STT_SAMPLE_RATE", 16000, STT_SAMPLE_RATES),
        prompt=env.str("SARVAM_STT_PROMPT"),
        high_vad_sensitivity=env.optional_bool("SARVAM_STT_HIGH_VAD_SENSITIVITY"),
        realtime_fallback=env.bool("SARVAM_STT_REALTIME_FALLBACK", True),
        vad_min_silence_ms=env.optional_int("SARVAM_STT_VAD_MIN_SILENCE_MS", 100, 5000),
    )


def _tts_settings(env: _Env) -> TTSSettings:
    model = env.str("SARVAM_TTS_MODEL", "bulbul:v3") or "bulbul:v3"
    speakers = MODEL_SPEAKER_COMPATIBILITY.get(model)
    if speakers is None:
        known = ", ".join(MODEL_SPEAKER_COMPATIBILITY)
        raise ConfigError(f"SARVAM_TTS_MODEL={model!r} must be one of: {known}")

    speaker = (env.str("SARVAM_TTS_SPEAKER", "kavitha") or "kavitha").lower()
    if speaker not in speakers["all"]:
        raise ConfigError(
            f"SARVAM_TTS_SPEAKER={speaker!r} is not a {model} voice. "
            f"Choose one of: {', '.join(speakers['all'])}"
        )

    return TTSSettings(
        model=model,
        language=env.str("SARVAM_TTS_LANGUAGE", "te-IN") or "te-IN",
        speaker=speaker,
        pace=env.float("SARVAM_TTS_PACE", 1.0, 0.3, 3.0),
        temperature=env.float("SARVAM_TTS_TEMPERATURE", 0.6, 0.01, 2.0),
        sample_rate=env.int_choice("SARVAM_TTS_SAMPLE_RATE", 22050, TTS_SAMPLE_RATES),
        min_buffer_size=env.int("SARVAM_TTS_MIN_BUFFER_SIZE", 50, 30, 200),
        max_chunk_length=env.int("SARVAM_TTS_MAX_CHUNK_LENGTH", 150, 50, 500),
        dict_id=env.str("SARVAM_TTS_DICT_ID"),
    )


def _llm_settings(env: _Env) -> LLMSettings:
    provider = env.choice("LLM_PROVIDER", "livekit", LLM_PROVIDERS)
    model = env.str("LLM_MODEL", DEFAULT_LLM_MODELS.get(provider))
    base_url = env.str("LLM_BASE_URL")
    api_key = env.str("LLM_API_KEY")

    if provider == "openai":
        api_key = api_key or env.str("OPENAI_API_KEY")
        if not api_key:
            raise ConfigError("LLM_PROVIDER=openai needs OPENAI_API_KEY")
    elif provider == "openai_compatible":
        missing = [
            name
            for name, value in (
                ("LLM_MODEL", model),
                ("LLM_BASE_URL", base_url),
                ("LLM_API_KEY", api_key),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                f"LLM_PROVIDER=openai_compatible needs {', '.join(missing)}"
            )

    assert model is not None
    return LLMSettings(
        provider=provider,
        model=model,
        temperature=env.float("LLM_TEMPERATURE", 0.3, 0.0, 2.0),
        max_tokens=env.int("LLM_MAX_TOKENS", 250, 32, 4096),
        base_url=base_url,
        api_key=api_key,
    )


def _turn_settings(env: _Env) -> TurnSettings:
    min_delay = env.float("TURN_ENDPOINTING_MIN_DELAY", 0.5, 0.0, 10.0)
    max_delay = env.float("TURN_ENDPOINTING_MAX_DELAY", 3.0, 0.0, 30.0)
    if max_delay < min_delay:
        raise ConfigError(
            "TURN_ENDPOINTING_MAX_DELAY must not be below TURN_ENDPOINTING_MIN_DELAY"
        )
    return TurnSettings(
        detection=env.choice("TURN_DETECTION", "vad", TURN_DETECTION_MODES),
        endpointing_min_delay=min_delay,
        endpointing_max_delay=max_delay,
        interruption_mode=env.choice("INTERRUPTION_MODE", "auto", INTERRUPTION_MODES),
        interruption_min_duration=env.float("INTERRUPTION_MIN_DURATION", 0.5, 0.0, 5.0),
        resume_false_interruption=env.bool("INTERRUPTION_RESUME_FALSE", True),
        preemptive_generation=env.bool("PREEMPTIVE_GENERATION", True),
    )


def load_settings(env: Mapping[str, str] | None = None) -> VoiceSettings:
    """Build validated settings from `env` (defaults to the process environment)."""
    source = os.environ if env is None else env
    reader = _Env(source)

    if not reader.str("SARVAM_API_KEY"):
        raise ConfigError(
            "SARVAM_API_KEY is not set. Copy .env.example to .env.local and fill it in."
        )

    metrics_log = reader.str("VOICE_METRICS_LOG", "logs/turn_metrics.jsonl")
    metrics_path = None
    if metrics_log and metrics_log.lower() not in ("off", "none", "false"):
        metrics_path = Path(metrics_log)
        if not metrics_path.is_absolute():
            metrics_path = PROJECT_ROOT / metrics_path

    return VoiceSettings(
        agent_name=reader.str("AGENT_NAME", "telugu-agent") or "telugu-agent",
        business_name=reader.str("BUSINESS_NAME", "this office") or "this office",
        greeting=reader.str("AGENT_GREETING", DEFAULT_GREETING) or DEFAULT_GREETING,
        fallback_message=reader.str("AGENT_FALLBACK_MESSAGE", DEFAULT_FALLBACK_MESSAGE)
        or DEFAULT_FALLBACK_MESSAGE,
        noise_cancellation=reader.bool("NOISE_CANCELLATION", True),
        can_end_call=reader.bool("AGENT_CAN_END_CALL", False),
        outbound_allowed_number=reader.str("OUTBOUND_TEST_ALLOWED_NUMBER"),
        log_level=reader.choice(
            "VOICE_LOG_LEVEL", "info", ("debug", "info", "warning", "error")
        ).upper(),
        log_transcripts=reader.bool("VOICE_LOG_TRANSCRIPTS", True),
        metrics_log_path=metrics_path,
        stt=_stt_settings(reader),
        tts=_tts_settings(reader),
        llm=_llm_settings(reader),
        turn=_turn_settings(reader),
    )
