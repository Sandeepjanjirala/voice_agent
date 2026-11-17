"""One JSON answer from the configured LLM, for drafting only.

This is an ordinary HTTPS request made from a Django view. It is not the voice
loop and imports nothing from the voice stack. It reads the same LLM_* settings
as the voice worker, so drafting needs no extra key:

    livekit            LiveKit Inference, signed with the project's API key
    openai             OpenAI, with OPENAI_API_KEY
    openai_compatible  LLM_BASE_URL + LLM_API_KEY + LLM_MODEL
"""

import json
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from livekit import api

LIVEKIT_INFERENCE_URL = "https://agent-gateway.livekit.cloud/v1"
OPENAI_URL = "https://api.openai.com/v1"
DEFAULT_MODELS = {"livekit": "google/gemma-4-31b-it", "openai": "gpt-4.1-mini"}
MAX_REPLY_BYTES = 200_000


class LLMError(Exception):
    """Drafting could not get a usable answer. The message is safe to log."""


def _endpoint() -> tuple[str, str, str]:
    """(base URL, bearer token, model) for the configured provider."""
    provider = settings.LLM_PROVIDER
    model = settings.EMPLOYEE_DRAFT_MODEL or settings.LLM_MODEL
    if provider == "livekit":
        key, secret = settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET
        if not (key and secret):
            raise LLMError("LiveKit credentials are not configured.")
        token = (
            api.AccessToken(key, secret)
            .with_identity("dashboard")
            .with_inference_grants(api.access_token.InferenceGrants(perform=True))
            .with_ttl(timedelta(minutes=5))
            .to_jwt()
        )
        base = settings.LIVEKIT_INFERENCE_URL or LIVEKIT_INFERENCE_URL
        return base, token, model or DEFAULT_MODELS["livekit"]
    if provider == "openai":
        if not settings.OPENAI_API_KEY:
            raise LLMError("OPENAI_API_KEY is not set.")
        return OPENAI_URL, settings.OPENAI_API_KEY, model or DEFAULT_MODELS["openai"]
    if provider == "openai_compatible":
        if not (settings.LLM_BASE_URL and settings.LLM_API_KEY and model):
            raise LLMError("LLM_BASE_URL, LLM_API_KEY and LLM_MODEL are required.")
        return settings.LLM_BASE_URL, settings.LLM_API_KEY, model
    raise LLMError(f"Unknown LLM_PROVIDER {provider!r}.")


def configured() -> bool:
    try:
        _endpoint()
    except LLMError:
        return False
    return True


def _json_object(text: str) -> dict:
    """The first JSON object in a reply, which may be wrapped in a code fence."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("The model did not return JSON.")
    try:
        value = json.loads(text[start : end + 1])
    except ValueError:
        raise LLMError("The model returned malformed JSON.") from None
    if not isinstance(value, dict):
        raise LLMError("The model did not return a JSON object.")
    return value


def complete_json(
    system: str, user: str, *, max_tokens: int = 1400, timeout: float = 45
) -> dict:
    """Ask the LLM for one JSON object. Raises LLMError on any failure."""
    base, token, model = _endpoint()
    if not base.startswith("https://"):
        raise LLMError("The LLM endpoint must use HTTPS.")
    body = json.dumps(
        {
            "model": model,
            "temperature": 0.4,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    ).encode()
    request = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(MAX_REPLY_BYTES))
    except urllib.error.HTTPError as error:
        raise LLMError(f"The LLM service answered HTTP {error.code}.") from None
    except (OSError, ValueError):
        raise LLMError("The LLM service could not be reached.") from None
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMError("The LLM service sent an unexpected reply.") from None
    if not isinstance(content, str):
        raise LLMError("The LLM service sent an empty reply.")
    return _json_object(content)
