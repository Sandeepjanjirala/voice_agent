"""Browser access only. The voice loop stays in the independent worker."""

import json
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST
from livekit import api

from apps.telephony.profiles import profile_metadata


def _agent_dispatch() -> api.RoomAgentDispatch:
    """Dispatch the existing agent, with the active agent profile if there is one.
    The profile holds wording and business facts only, never credentials."""
    profile = profile_metadata()
    if not profile:
        return api.RoomAgentDispatch(agent_name=settings.LIVEKIT_AGENT_NAME)
    return api.RoomAgentDispatch(
        agent_name=settings.LIVEKIT_AGENT_NAME, metadata=json.dumps(profile)
    )


@never_cache
@login_required
@ensure_csrf_cookie
def workspace(request):
    return render(request, "voice/workspace.html")


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
                agents=[_agent_dispatch()],
            )
        )
        .to_jwt()
    )
    return JsonResponse({"token": jwt, "url": settings.LIVEKIT_URL, "room": room})
