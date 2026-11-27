# ruff: noqa: F811
"""The dashboard reports local setup honestly without calling either provider."""

import pytest
from django.urls import reverse

from apps.calling.models import PhoneNumber

from .test_calling import BUSINESS_LINE, employee_for, world  # noqa: F401
from .test_telephony import PASSWORD, READY_ENV, TELEPHONY_ENV_NAMES

pytestmark = pytest.mark.django_db


@pytest.fixture
def inbound_config(monkeypatch, settings):
    for name in TELEPHONY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in READY_ENV.items():
        monkeypatch.setenv(name, value)
    # The dashboard route can supply the number, just as in the CLI plan.
    monkeypatch.delenv("TELEPHONY_INBOUND_NUMBER")
    monkeypatch.setenv(
        "CALL_EVENTS_URL", "http://127.0.0.1:8002/api/telephony/call-events/"
    )
    monkeypatch.setenv(
        "INBOUND_CONTEXT_URL", "http://127.0.0.1:8002/api/telephony/inbound-context/"
    )
    settings.CALL_EVENTS_SECRET = "test-event-signing-key-at-least-32-characters"
    settings.LIVEKIT_URL = "wss://test.livekit.cloud"
    settings.LIVEKIT_API_KEY = "test-api-key"
    settings.LIVEKIT_API_SECRET = "test-api-secret"
    settings.LIVEKIT_AGENT_NAME = "test-agent"
    return monkeypatch


def route(world, *, campus=None, number=BUSINESS_LINE):
    return PhoneNumber.objects.create(
        organisation=world.org_a,
        campus=campus or world.north,
        number=number,
        employee=employee_for(world),
        routing_enabled=True,
    )


@pytest.mark.parametrize("page", ["numbers", "inbound"])
def test_local_checks_include_dashboard_routes_without_claiming_live_verification(
    world, inbound_config, page
):
    route(world)
    response = world.client(world.admin_a).get(reverse(page))
    setup = response.context["incoming_setup"]
    assert setup["ready"] and setup["routed"] == setup["total"] == 1
    html = response.content.decode()
    assert "Ready for provider test" in html
    assert "live call audio have not been verified here" in html
    assert "Set TELEPHONY_INBOUND_NUMBER" not in html
    assert PASSWORD not in html and "test-api-secret" not in html
    assert "stays switched off until" not in html


def test_credentials_and_provider_confirmations_alone_do_not_make_inbound_ready(
    world, inbound_config
):
    response = world.client(world.admin_a).get(reverse("inbound"))
    setup = response.context["incoming_setup"]
    assert not setup["ready"] and setup["routed"] == 0
    assert "Setup incomplete" in response.content.decode()


@pytest.mark.parametrize("missing", ["CALL_EVENTS_URL", "INBOUND_CONTEXT_URL"])
def test_a_missing_worker_callback_keeps_setup_incomplete(
    world, inbound_config, missing
):
    route(world)
    inbound_config.delenv(missing)
    response = world.client(world.admin_a).get(reverse("inbound"))
    assert not response.context["incoming_setup"]["ready"]
    assert missing in response.content.decode()


def test_readiness_lists_all_authentication_blockers_and_the_vobiz_trial_limit(
    world, inbound_config
):
    route(world)
    inbound_config.setenv("TELEPHONY_PROVIDER", "vobiz")
    inbound_config.setenv("TELEPHONY_CONFIRMED", "")
    inbound_config.setenv("SIP_INBOUND_AUTH_MODE", "ip_allowlist")
    inbound_config.setenv("SIP_INBOUND_ALLOWED_ADDRESSES", "")
    response = world.client(world.admin_a).get(reverse("numbers"))
    assert not response.context["incoming_setup"]["ready"]
    html = response.content.decode()
    assert "SIP_INBOUND_ALLOWED_ADDRESSES" in html
    assert "LiveKit support confirmed allowed_addresses is enforced" in html
    assert "SRTP media encryption" in html
    assert "Vobiz trial numbers support outgoing calls only" in html
    assert "https://www.vobiz.ai/docs/faq/trial-inbound" in html


def test_a_paused_employee_is_not_counted_as_ready(world, inbound_config):
    number = route(world)
    number.employee.taking_calls = False
    number.employee.save(update_fields=["taking_calls"])
    response = world.client(world.admin_a).get(reverse("inbound"))
    setup = response.context["incoming_setup"]
    assert not setup["ready"] and setup["routed"] == 0 and setup["total"] == 1
    assert "Meera is paused." in response.content.decode()


def test_staff_only_see_their_routes_and_no_provider_configuration(
    world, inbound_config
):
    route(world, campus=world.south, number="+914055550001")
    response = world.client(world.staff_north).get(reverse("inbound"))
    setup = response.context["incoming_setup"]
    assert setup["routed"] == setup["total"] == 0
    assert not setup["ready"] and not setup["manage"] and not setup["checks"]
    html = response.content.decode()
    assert "+914055550001" not in html
    assert "SIP_INBOUND" not in html
    assert "Your organisation admin manages the provider connection" in html


def test_invalid_configuration_is_explained_without_breaking_the_page(
    world, inbound_config
):
    inbound_config.setenv("SIP_INBOUND_AUTH_MODE", "invalid")
    response = world.client(world.admin_a).get(reverse("inbound"))
    assert response.status_code == 200
    assert not response.context["incoming_setup"]["ready"]
    assert "SIP_INBOUND_AUTH_MODE must be one of" in response.content.decode()
