"""Hear an employee's voice without a phone call.

One text-to-speech request to Sarvam with the employee's voice, speed and
language. The audio is cached, so pressing Play again costs nothing. It goes
through the integrations transport, which checks the destination and applies
a timeout; tests replace that transport, so no test reaches Sarvam.
"""

import base64
import binascii
import hashlib
import json
import os

from django.conf import settings
from django.core.cache import cache

from apps.integrations import transport

from .voices import (
    DEFAULT_PACE,
    PACE_CHOICES,
    SPEECH_LANGUAGE,
    VOICE_MODELS,
    VOICE_NAMES,
)

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
SAMPLE_RATE = 22050
BITRATE = "64k"
MAX_AUDIO_BYTES = 1_500_000
CACHE_SECONDS = 24 * 3600
PACES = {value for value, _ in PACE_CHOICES}


class PreviewError(Exception):
    """No audio. The message is safe to show."""


def default_speaker() -> str:
    """The worker's default voice, as configured in the environment."""
    speaker = os.environ.get("SARVAM_TTS_SPEAKER", "").strip().lower()
    return speaker if speaker in VOICE_NAMES else "kavitha"


def options(text: str, speaker: str, pace: str, language: str) -> dict:
    """The request body, from values that may have come from a form."""
    text = " ".join(str(text or "").split())
    if not text:
        raise PreviewError("Write the opening line first.")
    speaker = speaker if speaker in VOICE_NAMES else default_speaker()
    pace = float(pace) if pace in PACES else DEFAULT_PACE
    return {
        "target_language_code": SPEECH_LANGUAGE.get(language, "te-IN"),
        "text": text,
        "speaker": speaker,
        "pace": pace,
        "speech_sample_rate": 24000
        if VOICE_MODELS[speaker] == "bulbul:v4-flash"
        else SAMPLE_RATE,
        "model": VOICE_MODELS[speaker],
        "output_audio_bitrate": BITRATE,
        "output_audio_codec": "mp3",
    }


def speak(body: dict) -> tuple[bytes, bool]:
    """MP3 audio for the request body, and whether it came from the cache."""
    key = (
        "voice-preview:"
        + hashlib.sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
    )
    audio = cache.get(key)
    if audio is not None:
        return audio, True
    if not settings.SARVAM_API_KEY:
        raise PreviewError("Voice previews need SARVAM_API_KEY in .env.local.")
    try:
        response = transport.send(
            "POST",
            SARVAM_TTS_URL,
            headers={
                "api-subscription-key": settings.SARVAM_API_KEY,
                "Content-Type": "application/json",
            },
            body=json.dumps(body, ensure_ascii=False).encode(),
            timeout=10,
            max_bytes=MAX_AUDIO_BYTES * 2,
        )
    except transport.TransportError as error:
        raise PreviewError(str(error)) from None
    if response.status != 200:
        raise PreviewError(f"The voice service answered {response.status}.")
    audios = (response.json() or {}).get("audios")
    if not isinstance(audios, list) or not audios:
        raise PreviewError("The voice service sent no audio.")
    try:
        audio = b"".join(base64.b64decode(part, validate=True) for part in audios)
    except (binascii.Error, TypeError, ValueError):
        raise PreviewError(
            "The voice service sent audio that could not be read."
        ) from None
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise PreviewError("The voice service sent no usable audio.")
    cache.set(key, audio, CACHE_SECONDS)
    return audio, False
