"""DEV_AUTO_LOGIN_USER: local development opens the dashboard signed in, and
nothing else changes (apps/core/middleware.py)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]

LOCAL = {"DEBUG": True, "DEV_AUTO_LOGIN_USER": "owner"}


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser("owner", password="x")


def signed_in_as(client):
    return client.session.get("_auth_user_id")


def test_off_by_default_the_login_page_is_shown(owner):
    client = Client()
    assert client.get(reverse("login")).status_code == 200
    assert signed_in_as(client) is None


def test_on_this_computer_the_dashboard_opens_signed_in(owner, settings):
    for name, value in LOCAL.items():
        setattr(settings, name, value)
    client = Client(REMOTE_ADDR="127.0.0.1")
    page = client.get(reverse("home"))
    assert signed_in_as(client) == str(owner.pk)
    # Never sent to the login page (a new superuser may be asked to set up
    # an organisation first).
    assert not page.get("Location", "").startswith(reverse("login"))


def test_the_login_page_goes_straight_on(owner, settings):
    for name, value in LOCAL.items():
        setattr(settings, name, value)
    client = Client(REMOTE_ADDR="::1")
    response = client.get(reverse("login") + "?next=/calls/")
    assert response.status_code == 302 and response["Location"] == "/calls/"
    # Another site is never a destination.
    away = client.get(reverse("login") + "?next=https://evil.example/")
    assert away["Location"] == reverse(settings.LOGIN_REDIRECT_URL)


@pytest.mark.parametrize(
    ("debug", "address", "path"),
    [
        (False, "127.0.0.1", "home"),  # DEBUG off
        (True, "192.168.1.20", "home"),  # another computer
        (True, "127.0.0.1", "telephony:calls"),  # the API keeps its own checks
    ],
)
def test_never_applies_elsewhere(owner, settings, debug, address, path):
    settings.DEBUG = debug
    settings.DEV_AUTO_LOGIN_USER = "owner"
    client = Client(REMOTE_ADDR=address)
    response = client.get(reverse(path))
    assert response.status_code in (302, 403)
    assert signed_in_as(client) is None


def test_an_unknown_or_inactive_user_leaves_the_login_page(owner, settings):
    for name, value in LOCAL.items():
        setattr(settings, name, value)
    owner.is_active = False
    owner.save()
    client = Client(REMOTE_ADDR="127.0.0.1")
    assert client.get(reverse("login")).status_code == 200
    assert signed_in_as(client) is None
    settings.DEV_AUTO_LOGIN_USER = "nobody"
    assert client.get(reverse("login")).status_code == 200


def test_a_server_without_debug_refuses_the_setting():
    # A separate process: loading the settings again here would also load
    # .env.local into this test run.
    env = {
        **os.environ,
        "DJANGO_DEBUG": "false",
        "DJANGO_SECRET_KEY": "test-only-secret-key",
        "DJANGO_ALLOWED_HOSTS": "example.org",
        "DEV_AUTO_LOGIN_USER": "owner",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import config.settings"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DEV_AUTO_LOGIN_USER is for local development only" in result.stderr
