"""Browser access only. The voice loop stays in the independent worker."""

import json
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST
from livekit import api

from apps.telephony.profiles import profile_metadata
from apps.tenancy.access import scope_for
from apps.workforce import publishing
from apps.workforce.models import Employee

TRIAL_SOURCES = {"draft": "draft", "published": "published version"}


def _trial(request) -> tuple[Employee, str] | None:
    """The employee a staff member asked to try, and which copy of it.
    None means an ordinary conversation with the active agent profile."""
    employee_id = request.GET.get("employee")
    scope = scope_for(request)
    if not employee_id or scope is None or not scope.can("employees.view"):
        return None
    try:
        employee = (
            scope.limit_shared(Employee.objects.all()).filter(pk=employee_id).first()
        )
    except (ValidationError, ValueError):
        employee = None
    if employee is None:
        return None
    source = request.GET.get("source")
    return employee, source if source in TRIAL_SOURCES else "draft"


def _agent_dispatch(profile: dict) -> api.RoomAgentDispatch:
    """Dispatch the existing agent, with a profile if there is one.
    The profile holds wording and business facts only, never credentials."""
    if not profile:
        return api.RoomAgentDispatch(agent_name=settings.LIVEKIT_AGENT_NAME)
    return api.RoomAgentDispatch(
        agent_name=settings.LIVEKIT_AGENT_NAME, metadata=json.dumps(profile)
    )


@never_cache
@login_required
@ensure_csrf_cookie
def workspace(request):
    token_url = reverse("voice-token")
    trial = _trial(request)
    context = {"token_url": token_url}
    if trial:
        employee, source = trial
        context.update(
            token_url=f"{token_url}?employee={employee.pk}&source={source}",
            trial_employee=employee,
            trial_source=TRIAL_SOURCES[source],
        )
    return render(request, "voice/workspace.html", context)


@never_cache
@require_POST
def token(request):
    if not request.user.is_authenticated or not request.user.is_active:
        return JsonResponse({"error": "Sign in to start a conversation."}, status=401)
    try:
        url = urlsplit(settings.LIVEKIT_URL)
    except ValueError:
        return JsonResponse(
            {"error": "Voice service is not configured. Contact the administrator."},
            status=503,
        )
    if (
        not all((settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET, url.hostname))
        or url.scheme != "wss"
    ):
        return JsonResponse(
            {"error": "Voice service is not configured. Contact the administrator."},
            status=503,
        )
    profile = profile_metadata()
    if request.GET.get("employee"):
        trial = _trial(request)
        if trial is None:
            return JsonResponse(
                {"error": "That employee cannot be tried from this account."},
                status=404,
            )
        try:
            profile = publishing.trial_metadata(*trial)
        except publishing.PublishError as error:
            reasons = " ".join(error.reasons)
            return JsonResponse(
                {"error": f"This employee is not ready to try. {reasons}"},
                status=400,
            )
    # Atomic within the local cache. Production requires a shared cache / gateway limit.
    if not cache.add(f"voice-token:{request.user.pk}", True, timeout=5):
        response = JsonResponse(
            {"error": "Please wait five seconds before starting again."}, status=429
        )
        response["Retry-After"] = "5"
        return response
    room = f"telugu-web-{uuid4().hex}"
    identity = f"browser-{uuid4().hex}"
    jwt = (
        api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_ttl(timedelta(minutes=5))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_sources=["microphone"],
                can_publish_data=False,
                can_update_own_metadata=False,
            )
        )
        .with_room_config(
            api.RoomConfiguration(
                max_participants=2,
                empty_timeout=60,
                departure_timeout=20,
                agents=[_agent_dispatch(profile)],
            )
        )
        .to_jwt()
    )
    return JsonResponse({"token": jwt, "url": settings.LIVEKIT_URL, "room": room})
