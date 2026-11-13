"""Offline stand-ins for model providers, so tests need no API keys or network."""

from __future__ import annotations

from typing import Any

from livekit.agents import APIConnectionError, llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

from voice_agent.config import VoiceSettings, load_settings

BASE_ENV = {"SARVAM_API_KEY": "test-sarvam-key", "VOICE_METRICS_LOG": "off"}


def make_settings(**overrides: str) -> VoiceSettings:
    return load_settings({**BASE_ENV, **overrides})


class _FakeStream(llm.LLMStream):
    def __init__(self, fake: FakeLLM, **kwargs: Any) -> None:
        super().__init__(fake, **kwargs)
        self._fake = fake

    async def _run(self) -> None:
        if self._fake.fail:
            raise APIConnectionError("fake LLM outage", retryable=False)
        self._event_ch.send_nowait(
            llm.ChatChunk(
                id="fake",
                delta=llm.ChoiceDelta(role="assistant", content=self._fake.reply),
            )
        )


class FakeLLM(llm.LLM):
    """Returns a canned reply, or fails every request when `fail` is set."""

    def __init__(self, reply: str = "సరేనండి.", *, fail: bool = False) -> None:
        super().__init__()
        self.reply = reply
        self.fail = fail
        self.requests: list[llm.ChatContext] = []

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[Any] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        **kwargs: Any,
    ) -> llm.LLMStream:
        # Snapshot: the session keeps appending to the live context afterwards.
        self.requests.append(chat_ctx.copy())
        return _FakeStream(
            self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options
        )
