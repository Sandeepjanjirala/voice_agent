"""Staff-only outbound calling page, its JSON API, and the worker report endpoint."""

import hmac
import json
import logging
import time

from django.conf import settings as django_settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_POST
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import calls, profiles
from .forms import AgentProfileForm
from .masking import mask_phone
from .models import AgentProfile, CallRecord

logger = logging.getLogger(__name__)

RECENT_CALLS = 20
START_THROTTLE_SECONDS = 5
EVENT_MAX_AGE_SECONDS = 300
EVENT_MAX_BYTES = 4096
SIGNATURE_HEADER = "HTTP_X_CALL_SIGNATURE"
TIMESTAMP_HEADER = "HTTP_X_CALL_TIMESTAMP"


def _error(error: calls.CallError) -> Response:
    return Response(
        {"error": str(error), "details": error.details}, status=error.http_status
    )


@never_cache
@login_required
@ensure_csrf_cookie
def calls_page(request):
    if not request.user.is_staff:
        raise PermissionDenied
    readiness: dict = {"ready": False, "blockers": [], "number": "", "caller_id": ""}
    try:
        outbound, plan = calls.outbound_context()
    except calls.CallNotReadyError as error:
        readiness["blockers"] = [str(error)]
    else:
        readiness.update(
            ready=plan.ready,
            blockers=[blocker.message for blocker in plan.blockers],
            number=mask_phone(outbound.allowed_number),
            caller_id=mask_phone(outbound.caller_id),
            ring_seconds=outbound.ringing_timeout_seconds,
            max_seconds=outbound.max_call_duration_seconds,
        )
        calls.close_stale(outbound)
    recent = list(CallRecord.objects.select_related("placed_by")[:RECENT_CALLS])
    active = next((record for record in recent if record.is_active), None)
    return render(
        request,
        "telephony/calls.html",
        {
            "readiness": readiness,
            "reporting": calls.reporting_configured(),
            "recent": [calls.serialize(record) for record in recent],
            "active_id": str(active.pk) if active else "",
        },
    )


@method_decorator(never_cache, name="dispatch")
class CallListView(APIView):
    """GET: recent calls. POST {"confirm": true}: place one call."""

    permission_classes = (IsAdminUser,)

    def get(self, request):
        calls.close_stale()
        recent = CallRecord.objects.select_related("placed_by")[:RECENT_CALLS]
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
        try:
            record = calls.start_call(user=request.user)
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
        record = CallRecord.objects.select_related("placed_by").filter(pk=pk).first()
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


@never_cache
@login_required
def agent_page(request):
    """Edit the active agent profile. Starts from an example with no business facts."""
    if not request.user.is_staff:
        raise PermissionDenied
    profile = profiles.active_profile()
    saved = False
    if request.method == "POST":
        form = AgentProfileForm(request.POST, instance=profile)
        if form.is_valid():
            profile = form.save(commit=False)
            AgentProfile.objects.exclude(pk=profile.pk).update(is_active=False)
            profile.is_active = True
            profile.save()
            saved = True
            form = AgentProfileForm(instance=profile)
    else:
        form = AgentProfileForm(
            instance=profile, initial=None if profile else profiles.EXAMPLE
        )
    return render(
        request,
        "telephony/agent.html",
        {"form": form, "profile": profile, "saved": saved},
    )
