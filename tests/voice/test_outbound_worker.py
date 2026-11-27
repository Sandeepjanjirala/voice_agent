"""The worker's side of the outbound test call, against a fake LiveKit job.

No call is placed. These cover the dial request, the one-number allowlist, the
mapping of SIP failures to outcomes, and that a failed call always shuts the job
down without ever starting a conversation.
"""

import json
from types import SimpleNamespace

import pytest
from livekit import api

from voice_agent import outbound

from .fakes import make_settings

MOBILE = "+919876543210"


def metadata(**overrides) -> str:
    request = {"to": MOBILE, "trunk_id": "ST_out", **overrides}
    return json.dumps({"outbound_call": request})


def sip_error(code: int, reason: str) -> api.SipCallError:
    return api.SipCallError(
        "unavailable",
        f"call to {MOBILE} failed",
        status=503,
        metadata={"sip_status_code": str(code), "sip_status": reason},
    )


class FakeJob:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[api.CreateSIPParticipantRequest] = []
        self.deleted_rooms: list[str] = []
        self.shutdown_reasons: list[str] = []
        self.shutdown_callbacks = []
        self.connected = False
        self.room = SimpleNamespace(
            name="outcall-test", remote_participants={}, on=lambda *a, **k: None
        )
        self.api = SimpleNamespace(
            sip=SimpleNamespace(create_sip_participant=self._create),
            room=SimpleNamespace(delete_room=self._delete_room),
        )

    async def _create(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error

    async def _delete_room(self, request):
        self.deleted_rooms.append(request.room)

    async def connect(self) -> None:
        self.connected = True

    def add_shutdown_callback(self, callback) -> None:
        self.shutdown_callbacks.append(callback)

    def shutdown(self, reason: str = "") -> None:
        self.shutdown_reasons.append(reason)

    async def wait_for_participant(self, *, identity=None):
        raise AssertionError("must not wait for a callee who never answered")


def settings_allowing(number: str | None = MOBILE):
    env = {"OUTBOUND_TEST_ALLOWED_NUMBER": number} if number else {}
    return make_settings(**env)


# --- dial request -----------------------------------------------------------


@pytest.mark.parametrize(
    "raw", [None, "", "not json", "[]", "{}", '{"store_id": 1}', '{"outbound_call": 5}']
)
def test_ordinary_jobs_are_not_outbound_calls(raw) -> None:
    assert outbound.parse_dial_request(raw) is None


def test_dial_request_is_read_and_limits_are_clamped() -> None:
    dial = outbound.parse_dial_request(
        metadata(ringing_timeout=9999, max_call_duration=99999)
    )

    assert (dial.to, dial.trunk_id) == (MOBILE, "ST_out")
    assert (dial.ringing_timeout, dial.max_call_duration) == (60, 600)


def test_broken_dial_request_can_never_dial() -> None:
    dial = outbound.parse_dial_request('{"outbound_call": {"to": "+911"}}')

    assert dial is not None and dial.to == "" and dial.trunk_id == ""


def test_allowed_number_is_not_in_settings_repr_or_startup_log() -> None:
    settings = settings_allowing()

    assert MOBILE not in repr(settings)
    assert MOBILE not in str(settings.describe())


# --- placing the call --------------------------------------------------------


async def test_answered_call_uses_the_trunk_hides_the_number_and_waits() -> None:
    job = FakeJob()

    result = await outbound.place_call(
        job, outbound.parse_dial_request(metadata()), settings_allowing()
    )

    assert result.outcome == outbound.ANSWERED
    (request,) = job.requests
    assert request.sip_trunk_id == "ST_out"
    assert request.sip_call_to == MOBILE
    assert request.room_name == "outcall-test"
    assert request.participant_identity == "callee"
    assert request.wait_until_answered is True
    assert request.hide_phone_number is True
    assert request.ringing_timeout.seconds == 30
    assert request.max_call_duration.seconds == 300
    # Credentials live on the stored trunk, never in the per-call request.
    assert not request.HasField("trunk")


@pytest.mark.parametrize(
    "allowed",
    [None, "+919876543211", "+15105550100"],
    ids=["unset", "other", "foreign"],
)
async def test_worker_refuses_any_number_but_the_allowed_one(allowed) -> None:
    job = FakeJob()

    result = await outbound.place_call(
        job, outbound.parse_dial_request(metadata()), settings_allowing(allowed)
    )

    assert result.outcome == outbound.REFUSED
    assert job.requests == []


@pytest.mark.parametrize(
    ("code", "reason", "expected"),
    [
        (486, "Busy Here", outbound.BUSY_OR_REJECTED),
        (603, "Decline", outbound.BUSY_OR_REJECTED),
        (480, "Temporarily Unavailable", outbound.NO_ANSWER),
        (408, "Request Timeout", outbound.NO_ANSWER),
        (503, "Service Unavailable", outbound.FAILED),
        (403, "Forbidden", outbound.FAILED),
    ],
)
async def test_sip_failures_map_to_outcomes_and_are_logged_masked(
    code, reason, expected, caplog
) -> None:
    job = FakeJob(error=sip_error(code, reason))

    with caplog.at_level("INFO", logger="voice_agent"):
        result = await outbound.place_call(
            job, outbound.parse_dial_request(metadata()), settings_allowing()
        )

    assert (result.outcome, result.sip_status_code) == (expected, code)
    assert len(job.requests) == 1  # no retry
    failure = next(r for r in caplog.records if "did not connect" in r.message)
    assert (failure.outcome, failure.sip_status_code) == (expected, code)
    assert all(MOBILE not in str(r.__dict__) for r in caplog.records)


async def test_the_failure_log_keeps_livekits_reason_with_the_number_masked(
    caplog,
) -> None:
    error = sip_error(403, "Forbidden")
    error.metadata["error_details"] = f"IP not allowed, dialling {MOBILE}"
    job = FakeJob(error=error)

    with caplog.at_level("INFO", logger="voice_agent"):
        await outbound.place_call(
            job, outbound.parse_dial_request(metadata()), settings_allowing()
        )

    failure = next(r for r in caplog.records if "did not connect" in r.message)
    assert "IP not allowed" in failure.detail
    assert "call to +91" in failure.detail
    assert MOBILE not in failure.detail


async def test_non_sip_errors_are_failures_not_crashes() -> None:
    job = FakeJob(error=TimeoutError("no route"))

    result = await outbound.place_call(
        job, outbound.parse_dial_request(metadata()), settings_allowing()
    )

    assert result.outcome == outbound.FAILED


# --- whole call --------------------------------------------------------------


@pytest.mark.parametrize(
    "error", [sip_error(486, "Busy Here"), sip_error(480, "Unavailable"), None]
)
async def test_unconnected_call_shuts_down_and_releases_the_room(
    error, monkeypatch
) -> None:
    def no_session(*args, **kwargs):
        raise AssertionError("the conversation must not start before an answer")

    monkeypatch.setattr(outbound, "build_session", no_session)
    job = FakeJob(error=error)
    # With no error the call is refused instead: no number is allowed.
    settings = settings_allowing(MOBILE if error else None)

    outcome = await outbound.run_outbound_call(
        job, settings, outbound.parse_dial_request(metadata())
    )

    assert outcome != outbound.ANSWERED
    assert job.connected
    assert job.shutdown_reasons == [f"outbound call {outcome}"]
    # The shutdown callback is what hangs up: run it as the job runner would.
    (hang_up,) = job.shutdown_callbacks
    await hang_up()
    assert job.deleted_rooms == ["outcall-test"]


async def test_a_stale_request_is_never_dialled(caplog) -> None:
    import time

    job = FakeJob()
    dial = outbound.parse_dial_request(metadata(requested_at=time.time() - 300))

    with caplog.at_level("WARNING", logger="voice_agent"):
        result = await outbound.place_call(job, dial, settings_allowing())

    assert result.outcome == outbound.EXPIRED
    assert job.requests == []
    assert "too old" in caplog.text
    assert MOBILE not in caplog.text


async def test_a_fresh_request_is_dialled() -> None:
    import time

    job = FakeJob()
    dial = outbound.parse_dial_request(metadata(requested_at=time.time() - 2))

    result = await outbound.place_call(job, dial, settings_allowing())

    assert result.outcome == outbound.ANSWERED
    assert len(job.requests) == 1
