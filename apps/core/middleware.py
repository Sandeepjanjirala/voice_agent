"""Local development only: open the dashboard already signed in.

With DJANGO_DEBUG=true and DEV_AUTO_LOGIN_USER set, a browser request from this
computer that is not signed in is signed in as that user, and the login page
goes straight on to where it was going. It never applies with DEBUG off, to a
request from another address, or to /api/ (the voice worker's signed calls
keep their own authentication). A deployed server always asks for
a password: config/settings.py refuses DEV_AUTO_LOGIN_USER without DEBUG.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.shortcuts import redirect, resolve_url
from django.utils.http import url_has_allowed_host_and_scheme

logger = logging.getLogger(__name__)

LOOPBACK = frozenset({"127.0.0.1", "::1"})


class DevAutoLoginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if applies(request):
            if not request.user.is_authenticated:
                _sign_in(request)
            if request.user.is_authenticated and request.path == resolve_url(
                settings.LOGIN_URL
            ):
                return redirect(_next(request))
        return self.get_response(request)


def applies(request) -> bool:
    return bool(
        settings.DEBUG
        and settings.DEV_AUTO_LOGIN_USER
        and request.META.get("REMOTE_ADDR") in LOOPBACK
        and not request.path.startswith("/api/")
    )


def _sign_in(request) -> None:
    user = (
        get_user_model()
        ._default_manager.filter(username=settings.DEV_AUTO_LOGIN_USER, is_active=True)
        .first()
    )
    if user is None:
        # The login page is shown as usual.
        logger.warning("DEV_AUTO_LOGIN_USER names no active user; sign in by hand")
        return
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")


def _next(request) -> str:
    target = request.GET.get("next", "")
    if url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return resolve_url(settings.LOGIN_REDIRECT_URL)
