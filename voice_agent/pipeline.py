"""Builds the STT -> LLM -> TTS cascade and turn handling from VoiceSettings."""

from __future__ import annotations

import logging

from livekit.agents import (
    AgentSession,
    TurnHandlingOptions,
    inference,
    llm,
    room_io,
    stt,
    tts,
)
from livekit.agents.voice.turn import EndpointingOptions, InterruptionOptions
from livekit.plugins import ai_coustics, openai, sarvam

from .config import LLMSettings, STTSettings, TTSSettings, TurnSettings, VoiceSettings

logger = logging.getLogger("voice_agent.pipeline")


def _standard_stt(settings: STTSettings) -> stt.STT:
    return sarvam.STT(
        language=settings.language,
        model=settings.model,
        mode=settings.task,
        sample_rate=settings.sample_rate,
        prompt=settings.prompt,
        high_vad_sensitivity=settings.high_vad_sensitivity,
    )


def build_stt(settings: STTSettings) -> stt.STT:
    if settings.mode == "standard":
        return _standard_stt(settings)

    realtime = sarvam.STTRealtime(
        language=settings.language,
        mode=settings.task,
        sample_rate=settings.sample_rate,
        prompt=settings.prompt,
        vad_min_silence_ms=settings.vad_min_silence_ms,
    )
    if not settings.realtime_fallback:
        return realtime

    # STTRealtime never reconnects after a socket drop (Sarvam bills per
    # connection), so a drop would otherwise end the conversation. Fall over to
    # the standard streaming API, which does reconnect.
    language = "unknown" if settings.language == "auto" else settings.language
    fallback = _standard_stt(
        STTSettings(
            mode="standard",
            model=settings.model,
            task=settings.task,
            language=language,
            sample_rate=settings.sample_rate,
            prompt=settings.prompt,
            high_vad_sensitivity=settings.high_vad_sensitivity,
            realtime_fallback=False,
            vad_min_silence_ms=None,
        )
    )
    return stt.FallbackAdapter([realtime, fallback])


def build_tts(settings: TTSSettings) -> tts.TTS:
    return sarvam.TTS(
        target_language_code=settings.language,
        model=settings.model,
        speaker=settings.speaker,
        pace=settings.pace,
        temperature=settings.temperature,
        speech_sample_rate=settings.sample_rate,
        min_buffer_size=settings.min_buffer_size,
        max_chunk_length=settings.max_chunk_length,
        dict_id=settings.dict_id,
    )


def build_llm(settings: LLMSettings) -> llm.LLM:
    if settings.provider == "livekit":
        # LiveKit Inference: billed through the LiveKit Cloud project, so it
        # needs no provider key of its own.
        return inference.LLM(
            model=settings.model,
            extra_kwargs={
                "temperature": settings.temperature,
                "max_completion_tokens": settings.max_tokens,
            },
        )

    kwargs = {}
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    return openai.LLM(
        model=settings.model,
        api_key=settings.api_key,
        temperature=settings.temperature,
        max_completion_tokens=settings.max_tokens,
        **kwargs,
    )


def build_audio_input(settings: VoiceSettings) -> room_io.AudioInputOptions:
    """Caller audio options. With NOISE_CANCELLATION on, background noise and
    other people's voices are removed before turn detection and STT hear them."""
    if not settings.noise_cancellation:
        return room_io.AudioInputOptions()
    # LiveKit Cloud only; set NOISE_CANCELLATION=false when self-hosting.
    return room_io.AudioInputOptions(
        noise_cancellation=ai_coustics.audio_enhancement(
            model=ai_coustics.EnhancerModel.QUAIL_VF_S
        ),
    )


def build_turn_handling(settings: TurnSettings) -> TurnHandlingOptions:
    interruption = InterruptionOptions(
        enabled=True,
        min_duration=settings.interruption_min_duration,
        min_words=settings.interruption_min_words,
        resume_false_interruption=settings.resume_false_interruption,
        false_interruption_timeout=settings.false_interruption_timeout,
    )
    # "auto" leaves the mode unset: the session uses adaptive interruption when
    # it runs against LiveKit Cloud and plain VAD barge-in otherwise.
    if settings.interruption_mode != "auto":
        interruption["mode"] = settings.interruption_mode

    options = TurnHandlingOptions(
        endpointing=EndpointingOptions(
            min_delay=settings.endpointing_min_delay,
            max_delay=settings.endpointing_max_delay,
        ),
        interruption=interruption,
        preemptive_generation={"enabled": settings.preemptive_generation},
    )
    if settings.detection == "livekit":
        # The LiveKit turn detector does not list Telugu among its supported
        # languages, so this is opt-in for experiments only.
        logger.warning(
            "TURN_DETECTION=livekit: the LiveKit turn detector does not officially "
            "support Telugu; compare against TURN_DETECTION=vad before relying on it"
        )
        options["turn_detection"] = inference.TurnDetector()
    else:
        options["turn_detection"] = settings.detection
    return options


def build_session(settings: VoiceSettings) -> AgentSession:
    return AgentSession(
        stt=build_stt(settings.stt),
        llm=build_llm(settings.llm),
        tts=build_tts(settings.tts),
        turn_handling=build_turn_handling(settings.turn),
    )
