"""Web outbound calling: records, status, worker reports, page and API.

LiveKit is replaced by FakeGateway; no test places a call or touches the network.
The worker-report tests sign with the worker's own code (voice_agent), so the
two processes are checked against one protocol.
"""

import json
import time
import uuid
from datetime import timedelta
from typing import ClassVar

import pytest
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from livekit import api

from apps.telephony import calls, outbound, providers
from apps.telephony.livekit_gateway import TelephonyGatewayError
from apps.telephony.models import CallRecord
from apps.tenancy.models import Campus, Membership, Organisation
from voice_agent.call_reporting import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    CallReporter,
    sign,
)

pytestmark = pytest.mark.django_db

MOBILE = "+919876543210"
CALLER_ID = "+918045550123"
PASSWORD = "s3cret-Outbound-pw!"
SECRET = "call-events-secret-for-tests-0123456789"
READY_ENV = {
    "SIP_OUTBOUND_ADDRESS": "abc123.sip.vobiz.ai",
    "SIP_OUTBOUND_AUTH_USERNAME": "lkoutbound",
    "SIP_OUTBOUND_AUTH_PASSWORD": PASSWORD,
    "SIP_OUTBOUND_CALLER_ID": CALLER_ID,
    "OUTBOUND_TEST_ALLOWED_NUMBER": MOBILE,
    "TELEPHONY_CONFIRMED": providers.OUTBOUND_READY,
}
ENV_NAMES = (*READY_ENV, "SIP_OUTBOUND_TRANSPORT", "SIP_OUTBOUND_MEDIA_ENCRYPTION")
Status = CallRecord.Status


class FakeGateway:
    """Duck-typed OutboundGateway. `state` drives what LiveKit reports."""

    def __init__(self) -> None:
        settings = outbound.load_outbound_settings()
        plan = outbound.build_outbound_plan(settings)
        trunk = outbound.outbound_trunk_request(plan, settings).trunk
        trunk.sip_trunk_id = "ST_out"
        self.trunk = trunk
        self.state: tuple[bool, str, bool] = (True, "dialing", True)
        self.rooms: list[str] = []
        self.dispatches: list[api.CreateAgentDispatchRequest] = []
        self.deleted: list[str] = []
        self.state_calls = 0
        self.fail: str = ""

    def _maybe_fail(self, name: str) -> None:
        if self.fail == name:
            raise TelephonyGatewayError(
                f"LiveKit refused {name} (unavailable). {MOBILE}"
            )

    async def trunks(self):
        return [self.trunk]

    async def active_test_rooms(self):
        return list(self.rooms)

    async def dispatch(self, request):
        self._maybe_fail("create_dispatch")
        self.dispatches.append(request)

    async def call_state(self, room):
        self.state_calls += 1
        self._maybe_fail("call_state")
        return self.state

    async def delete_room(self, room):
        self._maybe_fail("delete_room")
        self.deleted.append(room)


@pytest.fixture
def env(monkeypatch, settings):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in READY_ENV.items():
        monkeypatch.setenv(name, value)
    settings.LIVEKIT_URL = "wss://test.livekit.cloud"
    settings.LIVEKIT_API_KEY = "test-api-key"
    settings.LIVEKIT_API_SECRET = "test-secret-with-at-least-32-characters"
    settings.LIVEKIT_AGENT_NAME = "telugu-agent"
    settings.CALL_EVENTS_SECRET = SECRET
    cache.clear()
    return monkeypatch


@pytest.fixture
def gateway(env, monkeypatch):
    fake = FakeGateway()
    monkeypatch.setattr(calls, "make_gateway", lambda: fake)
    return fake


@pytest.fixture
def staff(django_user_model):
    return django_user_model.objects.create_user(
        username="operator", password="operator-password", is_staff=True
    )


@pytest.fixture
def staff_client(staff):
    client = Client()
    client.force_login(staff)
    return client


def age(record: CallRecord, seconds: float) -> None:
    CallRecord.objects.filter(pk=record.pk).update(
        created_at=timezone.now() - timedelta(seconds=seconds), last_checked_at=None
    )
    record.refresh_from_db()


# --- starting calls ---------------------------------------------------------------


def test_start_records_and_dispatches_only_the_allowed_number(gateway, staff) -> None:
    record = calls.start_call(user=staff)

    assert record.status == Status.INITIATING
    assert record.masked_number == "+91********10"
    assert record.placed_by == staff
    (dispatch,) = gateway.dispatches
    assert dispatch.agent_name == "telugu-agent"
    assert dispatch.room == record.room
    assert json.loads(dispatch.metadata)["outbound_call"]["to"] == MOBILE


def test_blocked_plan_creates_no_record_and_no_call(gateway, env) -> None:
    env.setenv("TELEPHONY_CONFIRMED", "")

    with pytest.raises(calls.CallNotReadyError) as raised:
        calls.start_call()

    assert raised.value.details
    assert CallRecord.objects.count() == 0
    assert gateway.dispatches == []


def test_second_call_is_refused_while_the_first_is_active(gateway) -> None:
    calls.start_call()

    with pytest.raises(calls.CallBusyError):
        calls.start_call()

    assert len(gateway.dispatches) == 1
    assert CallRecord.objects.count() == 1


def test_database_allows_only_one_active_outbound_call(env) -> None:
    CallRecord.objects.create(room="outcall-a", masked_number="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        CallRecord.objects.create(room="outcall-b", masked_number="x")
    # Finished calls and inbound calls are not limited.
    CallRecord.objects.create(room="outcall-c", masked_number="x", status=Status.FAILED)
    CallRecord.objects.create(
        room="call-d", masked_number="x", direction=CallRecord.Direction.INBOUND
    )


def test_a_finished_call_does_not_block_the_next(gateway) -> None:
    first = calls.start_call()
    CallRecord.objects.filter(pk=first.pk).update(status=Status.COMPLETED)

    second = calls.start_call()

    assert second.pk != first.pk
    assert second.room != first.room


def test_a_call_whose_room_is_gone_is_settled_before_the_next(gateway) -> None:
    first = calls.start_call()
    age(first, 30)
    gateway.state = (False, "", False)

    calls.start_call()

    first.refresh_from_db()
    assert (first.status, first.status_source) == (
        Status.FAILED,
        CallRecord.Source.INFERRED,
    )


def test_stale_active_record_is_closed_as_timed_out(gateway) -> None:
    first = calls.start_call()
    age(first, 3600)

    calls.start_call()

    first.refresh_from_db()
    assert (first.status, first.status_source) == (
        Status.FAILED,
        CallRecord.Source.TIMEOUT,
    )


def test_dispatch_failure_closes_the_record_and_frees_the_line(gateway) -> None:
    gateway.fail = "create_dispatch"

    with pytest.raises(calls.CallUpstreamError) as raised:
        calls.start_call()

    record = CallRecord.objects.get()
    assert record.status == Status.FAILED
    assert MOBILE not in str(raised.value) and MOBILE not in record.failure_reason
    gateway.fail = ""
    calls.start_call()


def test_open_livekit_test_room_means_busy(gateway) -> None:
    gateway.rooms = ["outcall-someone-else"]

    with pytest.raises(calls.CallBusyError, match="already in progress"):
        calls.start_call()

    assert CallRecord.objects.get().status == Status.FAILED


# --- following status from LiveKit ------------------------------------------------


def test_ringing_then_answered_from_room_state(gateway) -> None:
    record = calls.start_call()

    gateway.state = (True, "ringing", True)
    record = calls.refresh(record, force=True)
    assert record.status == Status.RINGING
    assert record.ringing_at is not None

    gateway.state = (True, "active", True)
    record = calls.refresh(record, force=True)
    assert record.status == Status.ANSWERED
    assert record.answered_at is not None


def test_room_gone_after_answer_is_completed_with_talk_time(gateway) -> None:
    record = calls.start_call()
    gateway.state = (True, "active", True)
    record = calls.refresh(record, force=True)
    CallRecord.objects.filter(pk=record.pk).update(
        answered_at=timezone.now() - timedelta(seconds=42)
    )

    gateway.state = (False, "", False)
    record = calls.refresh(record, force=True)

    assert (record.status, record.status_source) == (
        Status.COMPLETED,
        CallRecord.Source.INFERRED,
    )
    assert 41 <= record.duration_seconds <= 44


def test_missing_room_right_after_dispatch_is_not_read_as_over(gateway) -> None:
    record = calls.start_call()
    gateway.state = (False, "", False)

    record = calls.refresh(record, force=True)

    assert record.status == Status.INITIATING


def test_livekit_errors_never_end_a_call(gateway) -> None:
    record = calls.start_call()
    age(record, 60)
    gateway.fail = "call_state"

    record = calls.refresh(record, force=True)

    assert record.status == Status.INITIATING


def test_livekit_is_asked_at_most_once_a_second(gateway) -> None:
    record = calls.start_call()
    calls.refresh(record)
    record.refresh_from_db()
    calls.refresh(record)

    assert gateway.state_calls == 1


# --- worker reports -------------------------------------------------------------


def test_worker_answered_then_completed_with_metrics(gateway) -> None:
    record = calls.start_call()
    calls.apply_worker_event({"room": record.room, "event": "answered"})
    record = calls.apply_worker_event(
        {
            "room": record.room,
            "event": "ended",
            "outcome": "completed",
            "talk_seconds": 63.4,
            "agent_turns": 9,
            "user_turns": 8,
            "interruptions": 1,
            "e2e_latency_p50": 1.37,
            "e2e_latency_max": 1.77,
            "llm_ttft_p50": 0.36,
            "tts_ttfb_p50": 0.19,
        }
    )

    assert (record.status, record.status_source) == (
        Status.COMPLETED,
        CallRecord.Source.WORKER,
    )
    assert record.duration_seconds == 63.4
    assert (record.agent_turns, record.interruptions, record.e2e_latency_p50) == (
        9,
        1,
        1.37,
    )


@pytest.mark.parametrize(
    ("outcome", "code", "status"),
    [
        ("busy_or_rejected", 486, Status.REJECTED),
        ("no_answer", 480, Status.NO_ANSWER),
        ("failed", 503, Status.FAILED),
        ("refused", None, Status.FAILED),
    ],
)
def test_worker_failure_outcomes(gateway, outcome, code, status) -> None:
    record = calls.start_call()

    record = calls.apply_worker_event(
        {
            "room": record.room,
            "event": "ended",
            "outcome": outcome,
            "sip_status_code": code,
            "sip_status": f"Reason for {MOBILE}" if code else None,
        }
    )

    assert record.status == status
    assert record.sip_status_code == code
    assert record.failure_reason
    assert MOBILE not in record.sip_status


@pytest.mark.parametrize("code", [401, 403, 407])
def test_a_provider_refusal_names_the_trunk_settings_to_check(gateway, code) -> None:
    record = calls.start_call()

    record = calls.apply_worker_event(
        {
            "room": record.room,
            "event": "ended",
            "outcome": "failed",
            "sip_status_code": code,
            "sip_status": "Forbidden",
        }
    )

    assert record.status == Status.FAILED
    assert f"SIP {code}" in record.failure_reason
    assert "allows all of LiveKit's addresses" in record.failure_reason
    assert len(record.failure_reason) <= 200


def test_worker_report_corrects_an_inferred_status(gateway) -> None:
    record = calls.start_call()
    age(record, 30)
    gateway.state = (False, "", False)
    record = calls.refresh(record, force=True)
    assert record.status == Status.FAILED

    record = calls.apply_worker_event(
        {
            "room": record.room,
            "event": "ended",
            "outcome": "no_answer",
            "sip_status_code": 480,
        }
    )

    assert record.status == Status.NO_ANSWER


def test_operator_hang_up_is_kept_but_metrics_are_added(gateway) -> None:
    record = calls.start_call()
    gateway.state = (True, "active", True)
    record = calls.hang_up(calls.refresh(record, force=True))
    assert (record.status, record.status_source) == (
        Status.COMPLETED,
        CallRecord.Source.OPERATOR,
    )

    record = calls.apply_worker_event(
        {
            "room": record.room,
            "event": "ended",
            "outcome": "failed",
            "e2e_latency_p50": 1.4,
        }
    )

    assert record.status == Status.COMPLETED
    assert record.e2e_latency_p50 == 1.4


def test_duplicate_reports_are_harmless(gateway) -> None:
    record = calls.start_call()
    event = {"room": record.room, "event": "ended", "outcome": "busy_or_rejected"}

    calls.apply_worker_event(event)
    record = calls.apply_worker_event(event)

    assert record.status == Status.REJECTED
    assert CallRecord.objects.count() == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"room": "outcall-unknown", "event": "ended", "outcome": "failed"},
        {"event": "ended", "outcome": "failed"},
        {"room": "x", "event": "dialed"},
    ],
)
def test_invalid_reports_are_rejected(gateway, payload) -> None:
    calls.start_call()

    with pytest.raises(calls.CallEventError):
        calls.apply_worker_event(payload)


def test_bad_outcome_is_rejected(gateway) -> None:
    record = calls.start_call()

    with pytest.raises(calls.CallEventError):
        calls.apply_worker_event(
            {"room": record.room, "event": "ended", "outcome": "?"}
        )


def test_out_of_range_measurements_are_ignored(gateway) -> None:
    record = calls.start_call()

    record = calls.apply_worker_event(
        {
            "room": record.room,
            "event": "ended",
            "outcome": "completed",
            "talk_seconds": -5,
            "agent_turns": True,
            "e2e_latency_p50": float("nan"),
            "e2e_latency_max": 1e9,
            "sip_status_code": 99999,
        }
    )

    assert record.agent_turns is None
    assert record.e2e_latency_p50 is None and record.e2e_latency_max is None
    assert record.sip_status_code is None


# --- hanging up ---------------------------------------------------------------------


def test_hang_up_before_answer_cancels(gateway) -> None:
    record = calls.start_call()

    record = calls.hang_up(record)

    assert record.status == Status.FAILED
    assert "Cancelled" in record.failure_reason
    assert gateway.deleted == [record.room]


def test_hang_up_failure_keeps_the_call_active(gateway) -> None:
    record = calls.start_call()
    gateway.fail = "delete_room"

    with pytest.raises(calls.CallUpstreamError):
        calls.hang_up(record)

    record.refresh_from_db()
    assert record.is_active


def test_finished_call_cannot_be_hung_up(gateway) -> None:
    record = calls.start_call()
    record = calls.hang_up(record)

    with pytest.raises(calls.CallBusyError):
        calls.hang_up(record)


# --- API ---------------------------------------------------------------------------------


def test_api_refuses_anonymous_and_non_staff(gateway, django_user_model) -> None:
    url = reverse("telephony:calls")
    anonymous = Client()
    assert anonymous.get(url).status_code == 403
    assert (
        anonymous.post(
            url, {"confirm": True, "to": MOBILE}, content_type="application/json"
        ).status_code
        == 403
    )

    plain = Client()
    plain.force_login(
        django_user_model.objects.create_user(username="p", password="x-pass")
    )
    assert (
        plain.post(
            url, {"confirm": True, "to": MOBILE}, content_type="application/json"
        ).status_code
        == 403
    )
    assert gateway.dispatches == []


def test_api_enforces_csrf(gateway, staff) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(staff)
    # Any page with a form sets the CSRF cookie.
    client.get(reverse("login"))
    url = reverse("telephony:calls")

    rejected = client.post(
        url, {"confirm": True, "to": MOBILE}, content_type="application/json"
    )
    accepted = client.post(
        url,
        {"confirm": True, "to": MOBILE},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 201
    assert len(gateway.dispatches) == 1


@pytest.mark.parametrize("body", [{}, {"confirm": "true"}, {"confirm": 1}, []])
def test_api_requires_an_explicit_confirmation(gateway, staff_client, body) -> None:
    response = staff_client.post(
        reverse("telephony:calls"), body, content_type="application/json"
    )

    assert response.status_code == 400
    assert gateway.dispatches == []


def test_api_start_returns_full_number_without_credentials(
    gateway, staff_client
) -> None:
    response = staff_client.post(
        reverse("telephony:calls"),
        {"confirm": True, "to": MOBILE},
        content_type="application/json",
    )
    text = response.content.decode()

    assert response.status_code == 201
    assert "no-store" in response["Cache-Control"]
    body = response.json()["call"]
    assert body["status"] == "initiating" and body["masked_number"] == "+91********10"
    assert body["number"] == MOBILE
    for secret in (CALLER_ID, PASSWORD, SECRET, "ST_out"):
        assert secret not in text


def test_api_throttles_then_refuses_a_duplicate(gateway, staff_client) -> None:
    url = reverse("telephony:calls")
    staff_client.post(
        url, {"confirm": True, "to": MOBILE}, content_type="application/json"
    )

    quick = staff_client.post(
        url, {"confirm": True, "to": MOBILE}, content_type="application/json"
    )
    cache.clear()
    duplicate = staff_client.post(
        url, {"confirm": True, "to": MOBILE}, content_type="application/json"
    )

    assert quick.status_code == 429
    assert duplicate.status_code == 409
    assert len(gateway.dispatches) == 1


def test_api_not_ready_returns_reasons(gateway, env, staff_client) -> None:
    env.setenv("TELEPHONY_CONFIRMED", "")

    response = staff_client.post(
        reverse("telephony:calls"),
        {"confirm": True, "to": MOBILE},
        content_type="application/json",
    )

    assert response.status_code == 503
    assert response.json()["details"]


def test_api_status_follows_the_call(gateway, staff_client) -> None:
    created = staff_client.post(
        reverse("telephony:calls"),
        {"confirm": True, "to": MOBILE},
        content_type="application/json",
    ).json()["call"]
    url = reverse("telephony:call", args=[created["id"]])

    gateway.state = (True, "ringing", True)
    assert staff_client.get(url).json()["call"]["status"] == "ringing"
    CallRecord.objects.update(last_checked_at=None)
    gateway.state = (True, "active", True)
    assert staff_client.get(url).json()["call"]["status"] == "answered"


def test_api_unknown_call_is_404(gateway, staff_client) -> None:
    url = reverse("telephony:call", args=[uuid.uuid4()])

    assert staff_client.get(url).status_code == 404


def test_api_hang_up(gateway, staff_client) -> None:
    created = staff_client.post(
        reverse("telephony:calls"),
        {"confirm": True, "to": MOBILE},
        content_type="application/json",
    ).json()["call"]

    response = staff_client.post(
        reverse("telephony:hangup", args=[created["id"]]),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["call"]["active"] is False
    again = staff_client.post(
        reverse("telephony:hangup", args=[created["id"]]),
        content_type="application/json",
    )
    assert again.status_code == 409


@pytest.mark.parametrize("access", ["foreign_org", "foreign_campus", "unscoped_staff"])
def test_api_cannot_see_or_hang_up_a_call_outside_its_scope(
    gateway, staff, staff_client, access
) -> None:
    organisation = Organisation.objects.create(name="Tenant A")
    other = Organisation.objects.create(name="Tenant B")
    own_campus = Campus.objects.create(organisation=organisation, name="North")
    other_campus = Campus.objects.create(organisation=organisation, name="South")
    if access != "unscoped_staff":
        membership = Membership.objects.create(
            user=staff,
            organisation=organisation,
            role=Membership.Role.CAMPUS_STAFF,
        )
        membership.campuses.add(own_campus)
    record = CallRecord.objects.create(
        organisation=other if access == "foreign_org" else organisation,
        campus=other_campus if access == "foreign_campus" else None,
        room="outcall-hidden",
        masked_number="x",
    )

    listing = staff_client.get(reverse("telephony:calls"))
    detail = staff_client.get(reverse("telephony:call", args=[record.pk]))
    response = staff_client.post(reverse("telephony:hangup", args=[record.pk]))

    assert listing.json()["calls"] == []
    assert detail.status_code == response.status_code == 404
    assert gateway.deleted == []
    record.refresh_from_db()
    assert record.is_active


@pytest.mark.parametrize("role", Membership.Role.values)
def test_api_member_can_hang_up_a_call_in_its_scope(
    gateway, staff, staff_client, role
) -> None:
    organisation = Organisation.objects.create(name="Tenant A")
    campus = Campus.objects.create(organisation=organisation, name="North")
    membership = Membership.objects.create(
        user=staff, organisation=organisation, role=role
    )
    membership.campuses.add(campus)
    record = CallRecord.objects.create(
        organisation=organisation,
        campus=campus,
        room="outcall-visible",
        masked_number="x",
    )

    response = staff_client.post(reverse("telephony:hangup", args=[record.pk]))

    assert response.status_code == 200
    assert response.json()["call"]["active"] is False
    assert gateway.deleted == [record.room]


def test_api_lists_recent_calls(gateway, staff_client) -> None:
    calls.start_call()

    response = staff_client.get(reverse("telephony:calls"))

    assert [c["masked_number"] for c in response.json()["calls"]] == ["+91********10"]
    assert [c["number"] for c in response.json()["calls"]] == [MOBILE]


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_api_list_rejects_other_methods(gateway, staff_client, method) -> None:
    assert getattr(staff_client, method)(reverse("telephony:calls")).status_code == 405


# --- worker report endpoint -------------------------------------------------------------


def post_event(client: Client, payload, *, secret=SECRET, timestamp=None, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    timestamp = str(int(time.time())) if timestamp is None else timestamp
    return client.post(
        reverse("telephony:call-events"),
        data=body,
        content_type="application/json",
        **{
            "HTTP_" + TIMESTAMP_HEADER.upper().replace("-", "_"): timestamp,
            "HTTP_" + SIGNATURE_HEADER.upper().replace("-", "_"): sign(
                secret, timestamp, body
            ),
        },
    )


def test_signed_report_is_applied_without_csrf(gateway) -> None:
    record = calls.start_call()
    client = Client(enforce_csrf_checks=True)

    response = post_event(
        client, {"room": record.room, "event": "ended", "outcome": "no_answer"}
    )

    assert response.status_code == 204
    record.refresh_from_db()
    assert record.status == Status.NO_ANSWER


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({"secret": "wrong-secret-wrong-secret-wrong-secret"}, 403),
        ({"timestamp": str(int(time.time()) - 3600)}, 403),
        ({"timestamp": "not-a-time"}, 403),
        ({"raw": b"not json"}, 400),
        ({"raw": b"[1, 2]"}, 400),
        ({"raw": b"{" + b" " * 70_000 + b"}"}, 413),
    ],
)
def test_bad_reports_are_refused(gateway, kwargs, status) -> None:
    record = calls.start_call()

    response = post_event(
        Client(), {"room": record.room, "event": "ended", "outcome": "failed"}, **kwargs
    )

    assert response.status_code == status
    record.refresh_from_db()
    assert record.is_active


def test_reports_are_refused_until_a_secret_is_configured(gateway, settings) -> None:
    settings.CALL_EVENTS_SECRET = ""

    response = post_event(Client(), {"room": "x", "event": "answered"}, secret="")

    assert response.status_code == 503


def test_report_endpoint_is_post_only(gateway) -> None:
    assert Client().get(reverse("telephony:call-events")).status_code == 405


# --- whole workflow across both processes, with fakes at the edges ----------------------


class CapturingSession:
    """aiohttp.ClientSession stand-in that keeps the worker's exact request."""

    sent: ClassVar[list[dict]] = []

    def __init__(self, timeout=None) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, data, headers):
        CapturingSession.sent.append({"url": url, "body": data, "headers": headers})
        return _Reply(204)


class _Reply:
    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def deliver(worker: CallReporter, event: str, **fields) -> int:
    """Have the worker build and sign a report, then hand those exact bytes and
    headers to Django, as the HTTP request would."""
    import asyncio

    CapturingSession.sent = []
    asyncio.run(worker.send(event, **fields))
    (sent,) = CapturingSession.sent
    meta = {
        "HTTP_" + k.upper().replace("-", "_"): v
        for k, v in sent["headers"].items()
        if k != "Content-Type"
    }
    path = sent["url"].removeprefix("http://testserver")
    response = Client(enforce_csrf_checks=True).post(
        path, data=sent["body"], content_type="application/json", **meta
    )
    return response.status_code


def test_web_call_end_to_end_with_worker_reports(gateway, staff_client) -> None:
    created = staff_client.post(
        reverse("telephony:calls"),
        {"confirm": True, "to": MOBILE},
        content_type="application/json",
    ).json()["call"]
    status_url = reverse("telephony:call", args=[created["id"]])
    room = gateway.dispatches[0].room

    gateway.state = (True, "ringing", True)
    assert staff_client.get(status_url).json()["call"]["status"] == "ringing"

    worker = CallReporter(
        "http://testserver" + reverse("telephony:call-events"),
        SECRET,
        room,
        session_factory=CapturingSession,
    )
    assert deliver(worker, "answered") == 204
    assert staff_client.get(status_url).json()["call"]["status"] == "answered"

    assert (
        deliver(
            worker,
            "ended",
            outcome="completed",
            talk_seconds=58.2,
            agent_turns=7,
            user_turns=7,
            interruptions=1,
            e2e_latency_p50=1.4,
            e2e_latency_max=1.8,
            llm_ttft_p50=0.36,
            tts_ttfb_p50=0.19,
        )
        == 204
    )
    final = staff_client.get(status_url).json()["call"]
    assert final["status"] == "completed" and final["active"] is False
    assert final["duration_seconds"] == 58.2
    assert final["metrics"]["e2e_latency_p50"] == 1.4
    listed = staff_client.get(reverse("telephony:calls")).json()["calls"]
    assert listed[0]["id"] == created["id"]


def test_worker_report_with_a_different_secret_is_refused(gateway) -> None:
    record = calls.start_call()
    worker = CallReporter(
        "http://testserver" + reverse("telephony:call-events"),
        "another-secret-another-secret-another-secret",
        record.room,
        session_factory=CapturingSession,
    )

    assert deliver(worker, "ended", outcome="failed") == 403


# --- a call no voice agent picks up -----------------------------------------------


def test_call_nobody_picks_up_is_cancelled_and_its_job_withdrawn(gateway) -> None:
    record = calls.start_call()
    gateway.state = (True, "", False)  # room exists, no worker has joined
    age(record, 16)

    record = calls.refresh(record, force=True)

    assert (record.status, record.status_source) == (
        Status.FAILED,
        CallRecord.Source.DJANGO,
    )
    assert record.failure_reason == calls.NO_AGENT_REASON
    assert gateway.deleted == [record.room]


def test_waiting_briefly_for_the_agent_is_fine(gateway) -> None:
    record = calls.start_call()
    gateway.state = (True, "", False)

    record = calls.refresh(record, force=True)

    assert record.status == Status.INITIATING
    assert gateway.deleted == []


def test_cancelling_an_unclaimed_call_retries_if_livekit_fails(gateway) -> None:
    record = calls.start_call()
    gateway.state = (True, "", False)
    gateway.fail = "delete_room"
    age(record, 16)

    record = calls.refresh(record, force=True)

    assert record.status == Status.INITIATING


def test_expired_request_reported_by_the_worker(gateway) -> None:
    record = calls.start_call()

    record = calls.apply_worker_event(
        {"room": record.room, "event": "ended", "outcome": "expired"}
    )

    assert record.status == Status.FAILED
    assert "too old" in record.failure_reason


def test_dispatch_carries_the_request_time(gateway) -> None:
    before = time.time()
    calls.start_call()

    requested = json.loads(gateway.dispatches[0].metadata)["outbound_call"][
        "requested_at"
    ]

    assert before - 1 <= requested <= time.time() + 1
