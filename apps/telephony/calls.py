"""Outbound calls as records: start one, follow its status, record its outcome.

Wraps the tested outbound path in outbound.py. Status comes from two places:
LiveKit room state, polled while someone is watching (ringing, answered), and
signed reports from the voice worker (final outcome, SIP status, latency).
A worker report is authoritative; a status inferred from room state alone is
replaced when a report arrives. Nothing here ever dials a number other than
OUTBOUND_TEST_ALLOWED_NUMBER, and nothing retries.
"""

import math
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from asgiref.sync import async_to_sync
from django.conf import settings as django_settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.tenancy.access import organisation_of
from dialing import DialingPolicyError

from . import profiles
from .conf import TelephonyConfigError
from .livekit_gateway import TelephonyGatewayError
from .masking import mask_phone, mask_phones_in
from .models import CallRecord
from .outbound import (
    CallInProgressError,
    OutboundGateway,
    OutboundPlan,
    OutboundSettings,
    build_outbound_plan,
    load_outbound_settings,
    new_room_name,
    place_test_call,
)
from .provisioning import ProvisioningRefusedError

Status = CallRecord.Status
Source = CallRecord.Source

# A room may not exist for a moment after dispatch; do not read its absence
# as "call over" before this.
ROOM_GRACE = timedelta(seconds=20)
# A call no voice agent has joined by now is cancelled, so the waiting job
# cannot be picked up minutes later by a worker that starts afterwards.
AGENT_PICKUP_TIMEOUT = timedelta(seconds=15)
NO_AGENT_REASON = (
    "No voice agent picked up the call. Start the voice worker, then try again."
)
# LiveKit is asked about a call at most this often, however fast pages poll.
CHECK_INTERVAL = timedelta(seconds=1)
# Final statuses that a worker report may still correct.
SOFT_SOURCES = (Source.DJANGO, Source.LIVEKIT, Source.INFERRED, Source.TIMEOUT)

WORKER_OUTCOMES = {
    "completed": Status.COMPLETED,
    "busy_or_rejected": Status.REJECTED,
    "no_answer": Status.NO_ANSWER,
    "failed": Status.FAILED,
    "refused": Status.FAILED,
    "expired": Status.FAILED,
}
OUTCOME_REASONS = {
    Status.REJECTED: "The call was declined or the line was busy.",
    Status.NO_ANSWER: "Nobody answered before the ringing timeout.",
    Status.FAILED: "The call could not be connected.",
}
METRIC_FIELDS = {
    "agent_turns": (int, 0, 1000),
    "user_turns": (int, 0, 1000),
    "interruptions": (int, 0, 1000),
    "e2e_latency_p50": (float, 0, 60),
    "e2e_latency_max": (float, 0, 60),
    "llm_ttft_p50": (float, 0, 60),
    "tts_ttfb_p50": (float, 0, 60),
}


# Live mode only: the local hours (from, to) in which any call may start.
LIVE_CALLING_HOURS = (9, 21)


class CallError(Exception):
    """A call could not be started or changed. `http_status` suits an API reply."""

    http_status = 400

    def __init__(self, message: str, details: list[str] | None = None) -> None:
        super().__init__(mask_phones_in(message))
        self.details = [mask_phones_in(d) for d in details or []]


class CallNotReadyError(CallError):
    http_status = 503


class CallBusyError(CallError):
    http_status = 409


class CallUpstreamError(CallError):
    http_status = 502


class CallEventError(CallError):
    http_status = 400


# --- configuration ----------------------------------------------------------


def outbound_context() -> tuple[OutboundSettings, OutboundPlan]:
    try:
        outbound = load_outbound_settings()
    except TelephonyConfigError as error:
        raise CallNotReadyError(f"Outbound calling is misconfigured: {error}") from None
    return outbound, build_outbound_plan(outbound)


def make_gateway() -> OutboundGateway:
    """The LiveKit gateway used by the web pages. Tests replace this."""
    url = django_settings.LIVEKIT_URL
    key = django_settings.LIVEKIT_API_KEY
    secret = django_settings.LIVEKIT_API_SECRET
    if not (url and key and secret):
        raise CallNotReadyError("LiveKit is not configured on the server.")
    return OutboundGateway(url=url, api_key=key, api_secret=secret)


def call_limit(outbound: OutboundSettings) -> timedelta:
    """Longest a call can stay active before its record is closed as timed out."""
    return timedelta(
        seconds=outbound.ringing_timeout_seconds
        + outbound.max_call_duration_seconds
        + 60
    )


def reporting_configured() -> bool:
    return len(getattr(django_settings, "CALL_EVENTS_SECRET", "")) >= 32


# --- changing a record safely ---------------------------------------------------


def _update(record_id, change: Callable[[CallRecord], bool]) -> CallRecord:
    """Re-read the record under a row lock and apply `change` if it says so."""
    with transaction.atomic():
        record = CallRecord.objects.select_for_update().get(pk=record_id)
        if change(record):
            record.save()
    return record


def _talk_time(record: CallRecord, now) -> float | None:
    if record.answered_at is None:
        return None
    return round(max((now - record.answered_at).total_seconds(), 0.0), 1)


def _finish(record, status, source, *, reason: str = "", now=None) -> None:
    now = now or timezone.now()
    record.status = status
    record.status_source = source
    record.ended_at = record.ended_at or now
    if status == Status.COMPLETED and record.duration_seconds is None:
        record.duration_seconds = _talk_time(record, now)
    if reason:
        record.failure_reason = mask_phones_in(reason)[:200]


# --- starting a call ----------------------------------------------------------


def _check_live_hours(campus) -> None:
    """In live mode, calls go out only in reasonable hours, and only while the
    campus is open when the call is for a campus."""
    from apps.tenancy import hours

    opens, closes = LIVE_CALLING_HOURS
    if not opens <= timezone.localtime().hour < closes:
        raise CallError(
            f"Calls go out only between {opens:02d}:00 and {closes:02d}:00 in live mode."
        )
    if campus is not None and not hours.is_open(campus):
        raise CallError(f"{campus.name} is closed now. Try again in its opening hours.")


def start_call(
    *,
    user=None,
    to=None,
    contact=None,
    gateway: OutboundGateway | None = None,
    call_plan=None,
    campus=None,
    lead=None,
) -> CallRecord:
    """Record and place one call to the allow-listed number.

    With a `call_plan` (workforce.publishing.plan_call) the call uses that
    published AI employee version, and the record keeps the plan's snapshot of
    exactly what the worker was given; otherwise it uses the active agent
    profile, as before. `lead` links the call to the lead it rehearses. Either
    way only the allow-listed number is dialled.
    """
    outbound, plan = outbound_context()
    if not plan.ready:
        raise CallNotReadyError(
            "Outbound calling is not ready.",
            details=[blocker.message for blocker in plan.blockers],
        )
    # No destination means a rehearsal: it rings the rehearsal number. The phone
    # calls page, lead calls and campaigns always pass their destination.
    to = outbound.rehearsal_number if to is None else str(to).strip()
    try:
        to = outbound.policy.validate(to)
    except DialingPolicyError as error:
        raise CallError(str(error)) from None
    if to == outbound.caller_id:
        raise CallError("The destination must differ from the caller ID.")
    if outbound.policy.mode == "live":
        _check_live_hours(campus)
    gateway = gateway or make_gateway()

    # A finished call must not block the next one: settle any active record.
    close_stale(outbound)
    for active in CallRecord.objects.filter(
        direction=CallRecord.Direction.OUTBOUND, status__in=CallRecord.ACTIVE
    ):
        refresh(active, gateway=gateway, force=True)

    try:
        with transaction.atomic():
            record = CallRecord.objects.create(
                room=new_room_name(),
                masked_number=mask_phone(to),
                placed_by=user if getattr(user, "is_authenticated", False) else None,
                organisation=call_plan.version.employee.organisation
                if call_plan
                else organisation_of(user),
                campus=campus,
                lead=lead,
                contact=contact,
                employee_version=call_plan.version if call_plan else None,
                context_snapshot=call_plan.snapshot if call_plan else {},
            )
    except IntegrityError:
        raise CallBusyError(
            "A call is already in progress. Wait for it to end."
        ) from None

    try:
        async_to_sync(place_test_call)(
            plan,
            outbound,
            gateway,
            to=to,
            agent_name=django_settings.LIVEKIT_AGENT_NAME,
            room=record.room,
            extra_metadata=call_plan.metadata
            if call_plan
            else profiles.profile_metadata(),
        )
    except CallInProgressError as error:
        _close_failed(record, str(error))
        raise CallBusyError(str(error)) from None
    except ProvisioningRefusedError as error:
        _close_failed(record, str(error))
        raise CallNotReadyError(str(error)) from None
    except TelephonyGatewayError as error:
        _close_failed(record, str(error))
        raise CallUpstreamError(str(error)) from None
    return record


def _close_failed(record: CallRecord, reason: str) -> None:
    def change(locked: CallRecord) -> bool:
        if not locked.is_active:
            return False
        _finish(locked, Status.FAILED, Source.DJANGO, reason=reason)
        return True

    _update(record.pk, change)


# --- following a call ----------------------------------------------------------


def close_stale(outbound: OutboundSettings | None = None) -> int:
    """Close active records older than any call could last. Returns how many."""
    if outbound is None:
        try:
            outbound = load_outbound_settings()
        except TelephonyConfigError:
            return 0
    cutoff = timezone.now() - call_limit(outbound)
    closed = 0
    for record in CallRecord.objects.filter(
        status__in=CallRecord.ACTIVE, created_at__lt=cutoff
    ):

        def change(locked: CallRecord) -> bool:
            if not locked.is_active:
                return False
            _finish(
                locked,
                Status.FAILED,
                Source.TIMEOUT,
                reason="No final status arrived in time; closed automatically.",
            )
            return True

        _update(record.pk, change)
        closed += 1
    return closed


def refresh(
    record: CallRecord, *, gateway: OutboundGateway | None = None, force=False
) -> CallRecord:
    """Bring an active record up to date from LiveKit room state."""
    now = timezone.now()
    if not record.is_active:
        return record
    if (
        not force
        and record.last_checked_at
        and now - record.last_checked_at < CHECK_INTERVAL
    ):
        return record
    try:
        gateway = gateway or make_gateway()
        present, sip_status, agent = async_to_sync(gateway.call_state)(record.room)
    except (TelephonyGatewayError, CallNotReadyError):
        # Unknown is not "over": keep the status and try again next time.
        return _update(record.pk, lambda r: _touch(r, now))

    if (
        present
        and not agent
        and record.status == Status.INITIATING
        and now - record.created_at >= AGENT_PICKUP_TIMEOUT
    ):
        return _cancel_unclaimed(record, gateway, now)

    def change(locked: CallRecord) -> bool:
        locked.last_checked_at = now
        if not locked.is_active:
            return True
        if present:
            if sip_status == "ringing" and locked.status == Status.INITIATING:
                locked.status, locked.status_source = Status.RINGING, Source.LIVEKIT
                locked.ringing_at = locked.ringing_at or now
            elif sip_status == "active" and locked.status != Status.ANSWERED:
                locked.status, locked.status_source = Status.ANSWERED, Source.LIVEKIT
                locked.answered_at = locked.answered_at or now
        elif (
            now - locked.created_at >= ROOM_GRACE or locked.status != Status.INITIATING
        ):
            if locked.status == Status.ANSWERED:
                _finish(locked, Status.COMPLETED, Source.INFERRED, now=now)
            else:
                _finish(
                    locked,
                    Status.FAILED,
                    Source.INFERRED,
                    now=now,
                    reason="The call ended before it was answered; the voice "
                    "worker did not report why.",
                )
        return True

    return _update(record.pk, change)


def _cancel_unclaimed(record: CallRecord, gateway: OutboundGateway, now) -> CallRecord:
    """Delete the room, which withdraws the waiting job, and close the record."""
    try:
        async_to_sync(gateway.delete_room)(record.room)
    except TelephonyGatewayError:
        # Keep the record open; the next check tries again.
        return _update(record.pk, lambda r: _touch(r, now))

    def change(locked: CallRecord) -> bool:
        if locked.status != Status.INITIATING:
            return False
        _finish(locked, Status.FAILED, Source.DJANGO, reason=NO_AGENT_REASON, now=now)
        locked.last_checked_at = now
        return True

    return _update(record.pk, change)


def _touch(record: CallRecord, now) -> bool:
    record.last_checked_at = now
    return True


def hang_up(
    record: CallRecord, *, gateway: OutboundGateway | None = None
) -> CallRecord:
    """End an active call from the web page."""
    if not record.is_active:
        raise CallBusyError("This call has already ended.")
    gateway = gateway or make_gateway()
    try:
        async_to_sync(gateway.delete_room)(record.room)
    except TelephonyGatewayError as error:
        raise CallUpstreamError(str(error)) from None

    def change(locked: CallRecord) -> bool:
        if not locked.is_active:
            return False
        if locked.status == Status.ANSWERED:
            _finish(locked, Status.COMPLETED, Source.OPERATOR)
        else:
            _finish(
                locked,
                Status.FAILED,
                Source.OPERATOR,
                reason="Cancelled from the web page.",
            )
        return True

    return _update(record.pk, change)


# --- worker reports -----------------------------------------------------------


def _bounded(value: Any, kind, low, high):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = kind(value)
    except (TypeError, ValueError):
        return None
    if isinstance(number, float) and not math.isfinite(number):
        return None
    return number if low <= number <= high else None


def apply_worker_event(payload: dict) -> CallRecord:
    """Apply a verified report from the voice worker. Safe to receive twice."""
    room = payload.get("room")
    event = payload.get("event")
    if not isinstance(room, str) or event not in ("answered", "ended"):
        raise CallEventError("Unknown call event.")
    record = CallRecord.objects.filter(room=room).first()
    if record is None:
        raise CallEventError("No call record for that room.")
    now = timezone.now()

    if event == "answered":

        def change(locked: CallRecord) -> bool:
            soft_final = not locked.is_active and locked.status_source in SOFT_SOURCES
            if locked.status in (Status.INITIATING, Status.RINGING) or soft_final:
                locked.status, locked.status_source = Status.ANSWERED, Source.WORKER
                locked.answered_at = locked.answered_at or now
                locked.ended_at = None
                locked.failure_reason = ""
                return True
            return False

    else:
        outcome = WORKER_OUTCOMES.get(payload.get("outcome"))
        if outcome is None:
            raise CallEventError("Unknown call outcome.")
        metrics = {
            name: _bounded(payload.get(name), *limits)
            for name, limits in METRIC_FIELDS.items()
        }
        talk = _bounded(payload.get("talk_seconds"), float, 0, 3600)
        code = _bounded(payload.get("sip_status_code"), int, 100, 699)
        reason_text = payload.get("sip_status")
        sip_status = (
            mask_phones_in(reason_text)[:64] if isinstance(reason_text, str) else ""
        )

        def change(locked: CallRecord) -> bool:
            authoritative = locked.is_active or locked.status_source in SOFT_SOURCES
            if authoritative:
                if outcome == Status.COMPLETED and locked.answered_at is None:
                    locked.answered_at = now
                _finish(locked, outcome, Source.WORKER, now=now)
                if outcome != Status.COMPLETED:
                    reason = OUTCOME_REASONS[outcome]
                    if payload.get("outcome") == "refused":
                        reason = "The voice worker refused the number."
                    elif payload.get("outcome") == "expired":
                        reason = (
                            "The call request was too old when a voice worker "
                            "picked it up, so it was not dialled."
                        )
                    locked.failure_reason = reason
                else:
                    locked.failure_reason = ""
            # Measurements are kept even when an operator already closed the call.
            if talk is not None and (authoritative or locked.duration_seconds is None):
                locked.duration_seconds = talk
            if code is not None:
                locked.sip_status_code = code
                locked.sip_status = sip_status
            for name, value in metrics.items():
                if value is not None:
                    setattr(locked, name, value)
            return True

    try:
        record = _update(record.pk, change)
    except IntegrityError:
        # Re-opening this call would make two active calls; keep it as it is.
        record.refresh_from_db()
    if event == "ended" and "transcript" in payload:
        # Kept only if the call's organisation allows it (apps/calling).
        from apps.calling.analysis import store_transcript

        store_transcript(record, payload["transcript"])
    return record


# --- presentation -------------------------------------------------------------


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def serialize(record: CallRecord) -> dict[str, Any]:
    """Safe for the browser: masked number only, no credentials, no trunk data."""
    now = timezone.now()
    end = record.ended_at or now
    return {
        "id": str(record.pk),
        "room": record.room,
        "status": record.status,
        "status_label": record.get_status_display(),
        "active": record.is_active,
        "masked_number": record.masked_number,
        "placed_by": record.placed_by.get_username()
        if record.placed_by
        else "command line",
        "created_at": _iso(record.created_at),
        "ringing_at": _iso(record.ringing_at),
        "answered_at": _iso(record.answered_at),
        "ended_at": _iso(record.ended_at),
        "elapsed_seconds": round(max((end - record.created_at).total_seconds(), 0), 1),
        "duration_seconds": record.duration_seconds
        if record.duration_seconds is not None
        else (_talk_time(record, now) if record.status == Status.ANSWERED else None),
        "sip_status_code": record.sip_status_code,
        "sip_status": record.sip_status,
        "failure_reason": record.failure_reason,
        "status_source": record.status_source,
        "metrics": {name: getattr(record, name) for name in METRIC_FIELDS},
    }
