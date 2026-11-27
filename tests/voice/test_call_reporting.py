"""The worker's call-outcome reports to Django: signing, delivery and content.

No network: the HTTP session is a fake that records what would be sent.
"""

import hashlib
import hmac
import json
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest
from livekit.agents import llm

from voice_agent import outbound
from voice_agent.call_reporting import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    CallMetrics,
    CallReporter,
    sign,
)
from voice_agent.config import ConfigError

from .fakes import make_settings
from .test_outbound_worker import (
    MOBILE,
    FakeJob,
    metadata,
    settings_allowing,
    sip_error,
)

SECRET = "s" * 40
URL = "http://127.0.0.1:8000/api/telephony/call-events/"


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Stands in for aiohttp.ClientSession; records every POST."""

    sent: ClassVar[list[dict]] = []
    status = 204
    error: Exception | None = None

    def __init__(self, timeout=None) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, data, headers):
        if FakeSession.error:
            raise FakeSession.error
        FakeSession.sent.append({"url": url, "body": data, "headers": headers})
        return FakeResponse(FakeSession.status)


@pytest.fixture(autouse=True)
def fresh_session():
    FakeSession.sent, FakeSession.status, FakeSession.error = [], 204, None
    yield


def reporter(url=URL, secret=SECRET) -> CallReporter:
    return CallReporter(url, secret, "outcall-test", session_factory=FakeSession)


def test_signature_covers_timestamp_and_body() -> None:
    expected = hmac.new(SECRET.encode(), b"1700000000.{}", hashlib.sha256).hexdigest()

    assert sign(SECRET, "1700000000", b"{}") == expected
    assert sign(SECRET, "1700000001", b"{}") != expected


@pytest.mark.parametrize(("url", "secret"), [(None, SECRET), (URL, None), (None, None)])
async def test_reporting_is_off_without_url_and_secret(url, secret) -> None:
    sender = reporter(url, secret)

    assert sender.enabled is False
    assert await sender.send("ended", outcome="failed") is False
    assert FakeSession.sent == []
    assert sender.ended is True


async def test_report_is_signed_json_with_room_and_no_phone_number() -> None:
    assert await reporter().send(
        "ended", outcome="busy_or_rejected", sip_status_code=486
    )

    (sent,) = FakeSession.sent
    body = json.loads(sent["body"])
    assert sent["url"] == URL
    assert body == {
        "room": "outcall-test",
        "event": "ended",
        "outcome": "busy_or_rejected",
        "sip_status_code": 486,
    }
    timestamp = sent["headers"][TIMESTAMP_HEADER]
    assert sent["headers"][SIGNATURE_HEADER] == sign(SECRET, timestamp, sent["body"])
    assert MOBILE not in sent["body"].decode()


async def test_delivery_problems_never_reach_the_call(caplog) -> None:
    FakeSession.error = ConnectionRefusedError("django is down")
    assert await reporter().send("answered") is False

    FakeSession.error, FakeSession.status = None, 403
    with caplog.at_level("WARNING", logger="voice_agent"):
        assert await reporter().send("answered") is False
    assert "rejected by Django" in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.parametrize("event", ["ended", "recording"])
@pytest.mark.parametrize("status,attempts", [(503, 3), (403, 1)])
async def test_critical_callbacks_retry_server_failures_but_not_refusals(
    event, status, attempts, monkeypatch
):
    wait = AsyncMock()
    monkeypatch.setattr("voice_agent.call_reporting.asyncio.sleep", wait)
    FakeSession.status = status
    assert await reporter().send(event) is False
    assert len(FakeSession.sent) == attempts
    assert wait.await_count == attempts - 1
    for sent in FakeSession.sent:
        assert sent["headers"][SIGNATURE_HEADER] == sign(
            SECRET, sent["headers"][TIMESTAMP_HEADER], sent["body"]
        )


def _message(role: str, *, interrupted=False, **metrics) -> llm.ChatMessage:
    return llm.ChatMessage(
        role=role, content=["x"], interrupted=interrupted, metrics=metrics
    )


def test_metrics_summary_counts_turns_and_medians() -> None:
    collected = CallMetrics()
    for item in [
        _message("assistant", tts_node_ttfb=0.2),
        _message("user", end_of_turn_delay=0.7),
        _message("assistant", e2e_latency=1.3, llm_node_ttft=0.35, tts_node_ttfb=0.19),
        _message("user"),
        _message("assistant", interrupted=True, e2e_latency=1.7, llm_node_ttft=0.4),
    ]:
        collected.observe(item)

    assert collected.summary() == {
        "agent_turns": 3,
        "user_turns": 2,
        "interruptions": 1,
        "e2e_latency_p50": 1.5,
        "e2e_latency_max": 1.7,
        "llm_ttft_p50": 0.375,
        "tts_ttfb_p50": 0.195,
    }


def test_empty_call_summary_has_no_latency() -> None:
    summary = CallMetrics().summary()

    assert summary["agent_turns"] == 0
    assert summary["e2e_latency_p50"] is None


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"CALL_EVENTS_URL": "ftp://example.com"}, "CALL_EVENTS_URL"),
        ({"CALL_EVENTS_SECRET": "too-short"}, "CALL_EVENTS_SECRET"),
    ],
)
def test_bad_reporting_settings_are_rejected(env, message) -> None:
    with pytest.raises(ConfigError, match=message):
        make_settings(**env)


def test_reporting_secret_is_not_in_settings_repr() -> None:
    settings = make_settings(CALL_EVENTS_URL=URL, CALL_EVENTS_SECRET=SECRET)

    assert SECRET not in repr(settings)
    assert SECRET not in str(settings.describe())


async def test_failed_call_is_reported_with_its_sip_status_before_shutdown(
    monkeypatch,
) -> None:
    order: list = []

    class RecordingReporter(CallReporter):
        async def send(self, event, **fields):
            order.append(("report", event, fields))
            # Never the real HTTP client: a running dashboard is on this URL.
            self._session_factory = FakeSession
            return await super().send(event, **fields)

    monkeypatch.setattr(outbound, "CallReporter", RecordingReporter)
    job = FakeJob(error=sip_error(486, "Busy Here"))
    original_shutdown = job.shutdown
    job.shutdown = lambda reason="": (
        order.append(("shutdown", reason)),
        original_shutdown(reason),
    )
    settings = make_settings(
        OUTBOUND_TEST_ALLOWED_NUMBER=MOBILE,
        CALL_EVENTS_URL=URL,
        CALL_EVENTS_SECRET=SECRET,
    )

    outcome = await outbound.run_outbound_call(
        job, settings, outbound.parse_dial_request(metadata())
    )

    assert outcome == outbound.BUSY_OR_REJECTED
    assert order[0] == (
        "report",
        "ended",
        {
            "outcome": "busy_or_rejected",
            "sip_status_code": 486,
            "sip_status": "Busy Here",
        },
    )
    assert order[1][0] == "shutdown"
    # The room is deleted by the shutdown callback; no second report is sent.
    (hang_up,) = job.shutdown_callbacks
    await hang_up()
    assert [entry for entry in order if entry[0] == "report"] == [order[0]]


async def test_without_reporting_settings_a_failed_call_sends_nothing() -> None:
    job = FakeJob(error=sip_error(480, "Temporarily Unavailable"))

    await outbound.run_outbound_call(
        job, settings_allowing(), outbound.parse_dial_request(metadata())
    )

    assert FakeSession.sent == []
