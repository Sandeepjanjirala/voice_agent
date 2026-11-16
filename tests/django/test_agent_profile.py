# ruff: noqa: F811
"""Agent settings: the profile model, its page, and how it reaches the worker.

The payload is parsed with the worker's own code (voice_agent.profile), so the
two processes are checked against one format.
"""

import json

import jwt
import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from apps.telephony import calls, profiles
from apps.telephony.models import AgentProfile
from voice_agent.profile import (
    PROFILE_BUDGET_BYTES,
    parse_profile,
    profile_instructions,
)

from .test_calls import (  # noqa: F401
    FakeGateway,
    env,
    gateway,
    staff,
    staff_client,
)

pytestmark = pytest.mark.django_db


def make_profile(**overrides) -> AgentProfile:
    fields = {**profiles.EXAMPLE, "is_active": True, **overrides}
    return AgentProfile.objects.create(**fields)


def test_example_states_no_business_facts_and_no_booking_claims() -> None:
    example = profiles.EXAMPLE

    assert "business_name" not in example and "business_info" not in example
    assert "cannot book" in example["limitations"]
    assert "AI assistant" in example["greeting"]


def test_example_fits_the_workers_prompt_budget() -> None:
    profile = AgentProfile(**profiles.EXAMPLE)
    profile.full_clean(exclude=["name"])

    section = profile_instructions(
        parse_profile(json.dumps({"agent_profile": profile.payload()}))
    )

    assert "Purpose:" in section and "Limits:" in section
    assert len(section.encode("utf-8")) <= PROFILE_BUDGET_BYTES


def test_overlong_prompt_fields_are_rejected_with_a_clear_message() -> None:
    profile = AgentProfile(name="x", call_purpose="అ" * 300)

    with pytest.raises(ValidationError, match="too long together"):
        profile.full_clean()


def test_only_one_profile_can_be_active() -> None:
    make_profile()

    with pytest.raises(IntegrityError), transaction.atomic():
        make_profile(name="Second")
    make_profile(name="Third", is_active=False)


def test_no_profile_means_no_metadata() -> None:
    assert profiles.profile_metadata() == {}


def test_outbound_call_carries_the_active_profile(gateway) -> None:
    make_profile(business_name="Sunrise Clinic", business_info="Open 9 to 5.")

    calls.start_call()

    metadata = gateway.dispatches[0].metadata
    profile = parse_profile(metadata)
    assert profile.business_name == "Sunrise Clinic"
    assert profile.business_info == "Open 9 to 5."
    assert "appointment enquiry" in profile.purpose
    assert "outbound_call" in json.loads(metadata)


def test_outbound_call_without_a_profile_is_unchanged(gateway) -> None:
    calls.start_call()

    assert "agent_profile" not in json.loads(gateway.dispatches[0].metadata)


def test_browser_studio_token_carries_the_active_profile(
    staff_client, settings
) -> None:
    settings.LIVEKIT_URL = "wss://test.livekit.cloud"
    settings.LIVEKIT_API_KEY = "test-api-key"
    settings.LIVEKIT_API_SECRET = "test-secret-with-at-least-32-characters"
    cache.clear()
    make_profile()

    token = staff_client.post(reverse("voice-token")).json()["token"]
    claims = jwt.decode(token, settings.LIVEKIT_API_SECRET, algorithms=["HS256"])
    (dispatch,) = claims["roomConfig"]["agents"]

    assert dispatch["agentName"] == "telugu-agent"
    assert parse_profile(dispatch["metadata"]).purpose.startswith("You are calling")


def test_agent_page_needs_staff(django_user_model) -> None:
    client = Client()
    assert client.get(reverse("agent")).status_code == 302
    client.force_login(
        django_user_model.objects.create_user(username="p", password="x-pass")
    )
    assert client.get(reverse("agent")).status_code == 403


def test_agent_page_offers_the_example_without_saving_it(staff_client) -> None:
    html = staff_client.get(reverse("agent")).content.decode()

    assert "appointment enquiry" in html
    assert "Nothing is used until you press Save" in html
    assert AgentProfile.objects.count() == 0


def test_saving_activates_the_profile_and_editing_keeps_one(staff_client) -> None:
    data = {
        **profiles.EXAMPLE,
        "business_name": "Sunrise Clinic",
        "agent_name": "",
        "business_info": "Open 9 to 5, Monday to Saturday.",
    }

    first = staff_client.post(reverse("agent"), data)
    second = staff_client.post(
        reverse("agent"), {**data, "business_name": "Sunrise Dental"}
    )

    assert first.status_code == 200 and b"Saved" in first.content
    assert second.status_code == 200
    profile = AgentProfile.objects.get()
    assert profile.is_active and profile.business_name == "Sunrise Dental"


def test_invalid_profile_is_not_saved_and_shows_the_reason(staff_client) -> None:
    response = staff_client.post(
        reverse("agent"), {**profiles.EXAMPLE, "call_purpose": "అ" * 300}
    )

    assert b"too long together" in response.content
    assert AgentProfile.objects.count() == 0


def test_agent_page_enforces_csrf(staff) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(staff)

    assert client.post(reverse("agent"), profiles.EXAMPLE).status_code == 403


def test_profile_text_is_escaped_on_the_page(staff_client) -> None:
    make_profile(business_name='<script>alert("x")</script>')

    html = staff_client.get(reverse("agent")).content.decode()

    assert "<script>alert" not in html
