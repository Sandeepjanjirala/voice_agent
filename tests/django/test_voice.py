from datetime import UTC, datetime

import jwt
import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def configured(settings):
    settings.LIVEKIT_URL = "wss://test.livekit.cloud"
    settings.LIVEKIT_API_KEY = "test-api-key"
    settings.LIVEKIT_API_SECRET = "test-secret-with-at-least-32-characters"
    settings.LIVEKIT_AGENT_NAME = "telugu-agent"
    cache.clear()
    return settings


@pytest.fixture
def signed_in(client, django_user_model, configured):
    user = django_user_model.objects.create_user(
        username="tester", password="test-password"
    )
    client.force_login(user)
    return client


def test_workspace_requires_login(client):
    assert client.get(reverse("voice")).status_code == 302


def test_workspace_and_login_render(signed_in):
    response = signed_in.get(reverse("voice"))
    assert response.status_code == 200
    assert b"Start Conversation" in response.content
    assert b"livekit-client.umd.js" in response.content
    assert "no-store" in response["Cache-Control"]
    assert signed_in.get(reverse("login")).status_code == 200


def test_anonymous_cannot_issue_token(client, configured):
    assert client.post(reverse("voice-token")).status_code == 401


def test_token_is_post_only(signed_in):
    assert signed_in.get(reverse("voice-token")).status_code == 405


def test_token_grants_and_expiry(signed_in, configured):
    response = signed_in.post(
        reverse("voice-token"), {"room": "attacker-room", "agent_name": "other-agent"}
    )
    assert response.status_code == 200
    body = response.json()
    claims = jwt.decode(
        body["token"],
        configured.LIVEKIT_API_SECRET,
        algorithms=["HS256"],
        issuer=configured.LIVEKIT_API_KEY,
    )
    assert 290 <= claims["exp"] - datetime.now(UTC).timestamp() <= 300
    assert claims["video"] == {
        "roomJoin": True,
        "room": body["room"],
        "canPublish": True,
        "canSubscribe": True,
        "canPublishData": False,
        "canPublishSources": ["microphone"],
        "canUpdateOwnMetadata": False,
    }
    assert claims["roomConfig"]["agents"] == [{"agentName": "telugu-agent"}]
    assert claims["roomConfig"]["maxParticipants"] == 2
    assert "sip" not in claims
    assert claims["sub"].startswith("browser-")
    assert body["room"].startswith("telugu-web-")
    assert "no-store" in response["Cache-Control"]
    assert configured.LIVEKIT_API_SECRET not in response.content.decode()
    assert (
        configured.LIVEKIT_API_SECRET
        not in signed_in.get(reverse("voice")).content.decode()
    )


def test_sessions_get_isolated_rooms(signed_in):
    first = signed_in.post(reverse("voice-token")).json()
    cache.clear()
    second = signed_in.post(reverse("voice-token")).json()
    assert first["room"] != second["room"]
    assert first["token"] != second["token"]


def test_throttle(signed_in):
    assert signed_in.post(reverse("voice-token")).status_code == 200
    response = signed_in.post(reverse("voice-token"))
    assert response.status_code == 429
    assert response["Retry-After"] == "5"


@pytest.mark.parametrize(
    "setting,value",
    [
        ("LIVEKIT_API_KEY", ""),
        ("LIVEKIT_API_SECRET", ""),
        ("LIVEKIT_URL", "http://unsafe.example"),
        ("LIVEKIT_URL", "wss://[broken"),
        ("LIVEKIT_URL", ""),
    ],
)
def test_missing_config_fails_closed(signed_in, settings, setting, value):
    setattr(settings, setting, value)
    assert signed_in.post(reverse("voice-token")).status_code == 503


def test_csrf_is_enforced(signed_in):
    client = Client(enforce_csrf_checks=True)
    client.cookies = signed_in.cookies
    url = reverse("voice-token")
    assert client.post(url).status_code == 403
    client.get(reverse("voice"))
    csrf = client.cookies["csrftoken"].value
    assert (
        client.post(
            url, HTTP_X_CSRFTOKEN=csrf, HTTP_ORIGIN="https://evil.example"
        ).status_code
        == 403
    )
    assert client.post(url, HTTP_X_CSRFTOKEN=csrf).status_code == 200


def test_logout_requires_post(signed_in):
    assert signed_in.get(reverse("logout")).status_code == 405
    assert signed_in.post(reverse("logout")).status_code == 302
    assert signed_in.post(reverse("voice-token")).status_code == 401


def test_inactive_account_cannot_access_workspace_or_token(
    signed_in, django_user_model
):
    django_user_model.objects.filter(username="tester").update(is_active=False)
    assert signed_in.get(reverse("voice")).status_code == 302
    assert signed_in.post(reverse("voice-token")).status_code == 401


def test_login_rejects_bad_password_and_external_redirect(client, django_user_model):
    django_user_model.objects.create_user(
        username="login-test", password="right-password"
    )
    url = reverse("login")
    response = client.post(url, {"username": "login-test", "password": "wrong"})
    assert response.status_code == 200
    assert client.post(reverse("voice-token")).status_code == 401
    response = client.post(
        url,
        {
            "username": "login-test",
            "password": "right-password",
            "next": "https://evil.example/",
        },
    )
    assert response.status_code == 302
    # Sign-in lands on the dashboard home, which sends non-staff to the studio.
    assert response["Location"] == reverse("home")
    onward = client.get(reverse("home"))
    assert onward.status_code == 302
    assert onward["Location"] == reverse("voice")


def test_tokens_have_distinct_identities_and_reject_tampering(signed_in, configured):
    token = signed_in.post(reverse("voice-token")).json()["token"]
    claims = jwt.decode(token, configured.LIVEKIT_API_SECRET, algorithms=["HS256"])
    cache.clear()
    other = signed_in.post(reverse("voice-token")).json()["token"]
    other_claims = jwt.decode(
        other, configured.LIVEKIT_API_SECRET, algorithms=["HS256"]
    )
    assert claims["sub"] != other_claims["sub"]
    forged = jwt.encode(
        claims, "attacker-key-long-enough-for-hmac-sha256", algorithm="HS256"
    )
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(forged, configured.LIVEKIT_API_SECRET, algorithms=["HS256"])
    claims["exp"] = int(datetime.now(UTC).timestamp()) - 1
    expired = jwt.encode(claims, configured.LIVEKIT_API_SECRET, algorithm="HS256")
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(expired, configured.LIVEKIT_API_SECRET, algorithms=["HS256"])


def test_browser_security_headers_and_cookies(signed_in):
    response = signed_in.get(reverse("voice"))
    assert response["X-Frame-Options"] == "DENY"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Referrer-Policy"] == "same-origin"
    assert response.cookies["csrftoken"]["samesite"] == "Lax"
    # force_login bypasses middleware cookie creation; inspect a real login response.
    login = Client().post(
        reverse("login"), {"username": "tester", "password": "test-password"}
    )
    assert login.cookies["sessionid"]["httponly"]
    assert login.cookies["sessionid"]["samesite"] == "Lax"
    assert "Access-Control-Allow-Origin" not in response
    strict = Client(enforce_csrf_checks=True)
    assert (
        strict.post(
            reverse("login"), {"username": "tester", "password": "test-password"}
        ).status_code
        == 403
    )
