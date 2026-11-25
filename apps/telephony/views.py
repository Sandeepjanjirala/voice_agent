"""Staff-only outbound calling page, its JSON API, and the worker report endpoint."""

import hmac
import json
import logging
import time

from django.conf import settings as django_settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenancy.access import scope_for

from . import calls
from .models import CallRecord

logger = logging.getLogger(__name__)

RECENT_CALLS = 20
START_THROTTLE_SECONDS = 5
EVENT_MAX_AGE_SECONDS = 300
# A report may carry a transcript (voice_agent/call_reporting.py).
EVENT_MAX_BYTES = 65_536
SIGNATURE_HEADER = "HTTP_X_CALL_SIGNATURE"
TIMESTAMP_HEADER = "HTTP_X_CALL_TIMESTAMP"


def _error(error: calls.CallError) -> Response:
    return Response(
        {"error": str(error), "details": error.details}, status=error.http_status
    )


def _visible_calls(request):
    """Calls this person may see: their organisation's (and campuses'), or all
    of them for platform staff working outside any organisation."""
    scope = scope_for(request)
    found = CallRecord.objects.select_related("placed_by")
    if scope is not None and scope.organisation is not None:
        found = found.filter(organisation=scope.organisation)
        if not scope.all_campuses:
            found = found.filter(campus__in=scope.campus_ids)
    elif not request.user.is_staff:
        found = found.none()
    return found


@method_decorator(never_cache, name="dispatch")
class CallListView(APIView):
    """GET: recent calls. POST {"confirm": true}: place one call."""

    permission_classes = (IsAdminUser,)

    def get(self, request):
        calls.close_stale()
        recent = _visible_calls(request)[:RECENT_CALLS]
        return Response({"calls": [calls.serialize(record) for record in recent]})

    def post(self, request):
        if (
            not isinstance(request.data, dict)
            or request.data.get("confirm") is not True
        ):
            return Response(
                {"error": "Confirm the call first.", "details": []},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not cache.add(
            f"outbound-call:{request.user.pk}", True, timeout=START_THROTTLE_SECONDS
        ):
            response = Response(
                {
                    "error": "Please wait a few seconds before calling again.",
                    "details": [],
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            response["Retry-After"] = str(START_THROTTLE_SECONDS)
            return response
        from apps.calling import dialer
        from apps.workforce.models import Employee

        scope = scope_for(request)
        employee = None
        if request.data.get("employee") and scope and scope.organisation:
            employee = (
                scope.limit_shared(Employee.objects.all())
                .filter(pk=str(request.data["employee"]))
                .first()
            )
            if employee is None:
                return Response(
                    {"error": "Choose one of your published employees.", "details": []},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        try:
            if "slot" in request.data:
                # The page sends the position of an approved number, never a number.
                record = dialer.place_test(
                    scope, request.data["slot"], user=request.user, employee=employee
                ).call
            else:
                # The command line and older clients name the number; it must
                # still be an approved test number.
                record = calls.start_call(
                    user=request.user,
                    to=request.data.get("to", ""),
                    allowlisted_only=True,
                )
        except calls.CallError as error:
            logger.warning("outbound call not started: %s", error)
            return _error(error)
        logger.info("outbound call started", extra={"call_id": str(record.pk)})
        return Response(
            {"call": calls.serialize(record)}, status=status.HTTP_201_CREATED
        )


@method_decorator(never_cache, name="dispatch")
class CallDetailView(APIView):
    permission_classes = (IsAdminUser,)

    def get(self, request, pk):
        record = _visible_calls(request).filter(pk=pk).first()
        if record is None:
            return Response(
                {"error": "No such call."}, status=status.HTTP_404_NOT_FOUND
            )
        record = calls.refresh(record)
        return Response({"call": calls.serialize(record)})


@method_decorator(never_cache, name="dispatch")
class CallHangupView(APIView):
    permission_classes = (IsAdminUser,)

    def post(self, request, pk):
        record = CallRecord.objects.filter(pk=pk).first()
        if record is None:
            return Response(
                {"error": "No such call."}, status=status.HTTP_404_NOT_FOUND
            )
        try:
            record = calls.hang_up(record)
        except calls.CallError as error:
            return _error(error)
        return Response({"call": calls.serialize(record)})


def _signature_valid(request, secret: str) -> bool:
    signature = request.META.get(SIGNATURE_HEADER, "")
    timestamp = request.META.get(TIMESTAMP_HEADER, "")
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        return False
    if age > EVENT_MAX_AGE_SECONDS:
        return False
    message = timestamp.encode() + b"." + request.body
    expected = hmac.new(secret.encode(), message, "sha256").hexdigest()
    return hmac.compare_digest(expected, signature)


ROOM_HEADER = "HTTP_X_CALL_ROOM"
DURATION_HEADER = "HTTP_X_RECORDING_DURATION"
OFFSET_HEADER = "HTTP_X_RECORDING_OFFSET"


@csrf_exempt  # Server-to-server; authenticated by the HMAC signature instead.
@never_cache
@require_POST
def call_recording(request):
    """The audio of a recorded call, from the voice worker (voice_agent/recording.py).

    The body is the Ogg file. The signature covers the timestamp, the room, the
    duration, the offset and every byte of the file, with CALL_EVENTS_SECRET.
    Accepted only for a call Django allowed to be recorded and whose person
    agreed (apps/calling/recordings.py)."""
    from apps.calling import recordings

    secret = django_settings.CALL_EVENTS_SECRET
    if not calls.reporting_configured():
        return JsonResponse({"error": "Call reporting is not configured."}, status=503)
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if not 0 < length <= recordings.MAX_BYTES:
        return JsonResponse({"error": "Missing or too large."}, status=413)
    # Read directly: request.body is capped far below a recording's size.
    data = request.read(recordings.MAX_BYTES + 1)
    room = request.META.get(ROOM_HEADER, "")
    duration = request.META.get(DURATION_HEADER, "")
    offset = request.META.get(OFFSET_HEADER, "")
    timestamp = request.META.get(TIMESTAMP_HEADER, "")
    signature = request.META.get(SIGNATURE_HEADER, "")
    try:
        fresh = abs(time.time() - int(timestamp)) <= EVENT_MAX_AGE_SECONDS
    except ValueError:
        fresh = False
    message = f"{timestamp}.{room}.{duration}.{offset}.".encode() + data
    expected = hmac.new(secret.encode(), message, "sha256").hexdigest()
    if not fresh or not hmac.compare_digest(expected, signature):
        return JsonResponse({"error": "Invalid signature."}, status=403)
    record = CallRecord.objects.filter(room=room).first()
    if record is None:
        return JsonResponse({"error": "Unknown call."}, status=404)
    try:
        recording = recordings.receive(
            record, data, duration=_number(duration), offset=_number(offset)
        )
    except recordings.RecordingError as error:
        status_code = error.args[1] if len(error.args) > 1 else 400
        logger.warning("call recording refused", extra={"call_id": str(record.pk)})
        return JsonResponse({"error": str(error.args[0])}, status=status_code)
    logger.info("call recording stored", extra={"call_id": str(record.pk)})
    return JsonResponse({"status": recording.status}, status=201)


def _number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@csrf_exempt  # Server-to-server; authenticated by the HMAC signature instead.
@never_cache
@require_POST
def call_profile(request):
    """The employee profile of a call that is starting, for the voice worker.

    Two locks: the request is signed with CALL_EVENTS_SECRET, and it carries
    the call's own short-lived reference (calls.profile_reference). The answer
    is the call's immutable snapshot, never the employee's current draft."""
    if not calls.reporting_configured():
        return JsonResponse({"error": "Call reporting is not configured."}, status=503)
    if len(request.body) > 2048:
        return JsonResponse({"error": "Request too large."}, status=413)
    if not _signature_valid(request, django_settings.CALL_EVENTS_SECRET):
        return JsonResponse({"error": "Invalid signature."}, status=403)
    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    record = calls.record_for_reference(data)
    if record is None:
        return JsonResponse({"error": "Unknown or expired reference."}, status=404)
    return JsonResponse({"agent_profile": record.context_snapshot["profile"]})


@csrf_exempt  # Server-to-server; authenticated by the HMAC signature instead.
@never_cache
@require_POST
def call_events(request):
    """Outcome reports from the voice worker (voice_agent/call_reporting.py)."""
    secret = django_settings.CALL_EVENTS_SECRET
    if not calls.reporting_configured():
        return JsonResponse({"error": "Call reporting is not configured."}, status=503)
    if len(request.body) > EVENT_MAX_BYTES:
        return JsonResponse({"error": "Report too large."}, status=413)
    if not _signature_valid(request, secret):
        return JsonResponse({"error": "Invalid signature."}, status=403)
    try:
        payload = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Invalid report."}, status=400)
    try:
        record = calls.apply_worker_event(payload)
    except calls.CallEventError as error:
        return JsonResponse({"error": str(error)}, status=400)
    logger.info(
        "call event applied",
        extra={"call_id": str(record.pk), "call_status": record.status},
    )
    return HttpResponse(status=204)


@csrf_exempt  # Server-to-server; authenticated by the HMAC signature instead.
@never_cache
@require_POST
def inbound_context(request):
    """Which published AI employee answers an inbound call (voice_agent/inbound.py).

    404 means the number is not routed: the worker then answers with its own
    profile. The caller's number is used to recognise a known contact and is
    never logged or returned.
    """
    secret = django_settings.CALL_EVENTS_SECRET
    if not calls.reporting_configured():
        return JsonResponse({"error": "Call reporting is not configured."}, status=503)
    if len(request.body) > EVENT_MAX_BYTES:
        return JsonResponse({"error": "Request too large."}, status=413)
    if not _signature_valid(request, secret):
        return JsonResponse({"error": "Invalid signature."}, status=403)
    try:
        payload = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    if not isinstance(payload, dict) or not isinstance(payload.get("room"), str):
        return JsonResponse({"error": "Invalid request."}, status=400)
    from apps.calling import inbound

    metadata = inbound.answer(
        room=payload["room"][:64],
        called=str(payload.get("called") or ""),
        caller=str(payload.get("caller") or ""),
    )
    if metadata is None:
        return JsonResponse({"error": "This number is not routed."}, status=404)
    return JsonResponse(metadata)
