"""Outbound destinations under the dialing policy: the phone calls page,
calling a lead, and dispatching one campaign entry, in allowlist and live
modes. LiveKit is the fake gateway from test_calls; no call leaves the test."""

# The fixtures imported from test_calls are named as test parameters.
# ruff: noqa: F811

from datetime import datetime, time, timedelta

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.calling import outbound as calling_outbound
from apps.calling.models import Campaign, QueuedCall
from apps.crm.models import Contact
from apps.telephony import calls
from apps.telephony.models import CallRecord
from apps.tenancy.models import AuditEvent, OpeningHours
from voice_agent.config import load_settings

from .test_calling import employee_for
from .test_calls import (  # noqa: F401 - pytest fixtures
    CALLER_ID,
    MOBILE,
    env,
    gateway,
    staff,
    staff_client,
)
from .test_tenancy_crm import ASHA, RAVI, World, assert_no_full_number, body

pytestmark = pytest.mark.django_db

SECOND = "+919876543299"
FOREIGN = "+442079460123"


@pytest.fixture
def at_eleven(monkeypatch):
    """Now is 11:00 today, India time: inside every calling window. Today, so
    that signed-in test sessions have not expired."""
    fixed = timezone.make_aware(datetime.combine(timezone.localdate(), time(11, 0)))
    monkeypatch.setattr(timezone, "now", lambda: fixed)
    return fixed


def policy(env, mode="allowlist", numbers=f"{MOBILE},{SECOND}"):
    env.setenv("OUTBOUND_DIALING_MODE", mode)
    env.setenv("OUTBOUND_ALLOWLIST_NUMBERS", numbers)


def start(client, to):
    return client.post(
        reverse("telephony:calls"),
        {"confirm": True, "to": to},
        content_type="application/json",
    )


def dialled(gateway) -> list[str]:
    import json

    return [
        json.loads(request.metadata)["outbound_call"]["to"]
        for request in gateway.dispatches
    ]


# --- the phone calls page -------------------------------------------------------------


def test_allowlist_mode_dials_any_approved_number_typed_on_the_page(
    env, gateway, staff_client
):
    policy(env)
    response = start(staff_client, SECOND)
    assert response.status_code == 201
    assert response.json()["call"]["masked_number"] == "+91********99"
    assert dialled(gateway) == [SECOND]
    assert SECOND not in response.content.decode()


@pytest.mark.parametrize("to", [RAVI, FOREIGN])
def test_allowlist_mode_refuses_other_numbers_before_livekit(
    env, gateway, staff_client, to
):
    policy(env)
    response = start(staff_client, to)
    assert response.status_code == 400
    assert "not in OUTBOUND_ALLOWLIST_NUMBERS" in response.json()["error"]
    assert gateway.dispatches == [] and not CallRecord.objects.exists()


@pytest.mark.parametrize("mode", ["allowlist", "live"])
@pytest.mark.parametrize("to", ["", "98765 43210", "+91 98765 43210", "0919876543210"])
def test_malformed_numbers_never_dial(env, gateway, staff_client, mode, to, at_eleven):
    policy(env, mode)
    response = start(staff_client, to)
    assert response.status_code == 400
    assert "international format" in response.json()["error"]
    assert gateway.dispatches == []


def test_live_mode_dials_a_number_not_on_any_list(
    env, gateway, staff_client, at_eleven
):
    policy(env, "live", numbers="")
    response = start(staff_client, RAVI)
    assert response.status_code == 201
    assert dialled(gateway) == [RAVI]


def test_live_mode_keeps_to_calling_hours(env, gateway, staff_client, monkeypatch):
    policy(env, "live", numbers="")
    late = timezone.make_aware(datetime.combine(timezone.localdate(), time(22, 30)))
    monkeypatch.setattr(timezone, "now", lambda: late)
    response = start(staff_client, RAVI)
    assert response.status_code == 400
    assert "between 09:00 and 21:00" in response.json()["error"]
    assert gateway.dispatches == []


def test_the_caller_id_is_never_a_destination(env, gateway, staff_client, at_eleven):
    policy(env, "live", numbers="")
    response = start(staff_client, CALLER_ID)
    assert response.status_code == 400 and gateway.dispatches == []


def test_the_page_names_the_mode_and_never_shows_a_full_number(
    env, gateway, staff_client
):
    policy(env)
    page = body(staff_client.get(reverse("calls")))
    assert 'id="phone-number"' in page and "Allowlist" in page
    for number in (MOBILE, SECOND):
        assert number not in page
    policy(env, "live")
    assert "Live dialing" in body(staff_client.get(reverse("calls")))


def test_rehearsals_ring_the_first_allowlisted_number(env, gateway):
    env.delenv("OUTBOUND_TEST_ALLOWED_NUMBER")
    policy(env, numbers=f"{SECOND},{MOBILE}")
    record = calls.start_call()
    assert dialled(gateway) == [SECOND]
    assert record.masked_number == "+91********99"


def test_an_unknown_mode_stops_outbound_calling(env, gateway, staff_client):
    env.setenv("OUTBOUND_DIALING_MODE", "LIVE")
    response = start(staff_client, MOBILE)
    assert response.status_code >= 400 and gateway.dispatches == []


# --- the worker applies the same policy ------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "numbers", "to", "allowed"),
    [
        ("allowlist", f"{MOBILE},{SECOND}", SECOND, True),
        ("allowlist", f"{MOBILE},{SECOND}", RAVI, False),
        ("live", "", RAVI, True),
        ("live", "", "98765 43210", False),
    ],
)
def test_the_worker_reads_the_same_policy(mode, numbers, to, allowed):
    from dialing import DialingPolicyError

    worker = load_settings(
        {
            "SARVAM_API_KEY": "test-key",
            "OUTBOUND_DIALING_MODE": mode,
            "OUTBOUND_ALLOWLIST_NUMBERS": numbers,
        }
    )
    if allowed:
        assert worker.outbound_policy.validate(to) == to
    else:
        with pytest.raises(DialingPolicyError):
            worker.outbound_policy.validate(to)


# --- calling a lead -------------------------------------------------------------------


@pytest.fixture
def world():
    return World()


def lead_for(world, number=ASHA):
    from .test_calling import consented

    contact = consented(world, world.north, number)
    return contact.leads.create(
        organisation=world.org_a, campus=world.north, subject="Campus visit"
    )


def call_lead(world, lead, employee):
    return world.client(world.admin_a).post(
        reverse("lead-call", args=[lead.pk]),
        {"employee": str(employee.pk)},
        follow=True,
    )


def test_calling_a_lead_stays_off_until_contact_dialing_is_on(
    env, gateway, world, settings
):
    settings.CONTACT_DIALING_ENABLED = False
    policy(env, numbers=f"{MOBILE},{ASHA}")
    lead, employee = lead_for(world), employee_for(world)
    page = body(world.client(world.admin_a).get(reverse("lead-detail", args=[lead.pk])))
    assert 'id="lead-call-off"' in page and 'id="lead-call"' not in page
    response = call_lead(world, lead, employee)
    assert "switched off on this installation" in body(response)
    assert gateway.dispatches == []


def test_calling_a_lead_uses_its_own_number_under_the_policy(
    env, gateway, world, settings
):
    settings.CONTACT_DIALING_ENABLED = True
    policy(env, numbers=f"{MOBILE},{ASHA}")
    lead, employee = lead_for(world), employee_for(world)
    page = body(world.client(world.admin_a).get(reverse("lead-detail", args=[lead.pk])))
    assert 'id="lead-call"' in page
    assert_no_full_number(page)

    response = call_lead(world, lead, employee)
    record = CallRecord.objects.get()
    assert dialled(gateway) == [ASHA]
    assert (record.contact, record.lead, record.campus) == (
        lead.contact,
        lead,
        world.north,
    )
    assert record.employee_version == employee.live_version
    assert AuditEvent.objects.filter(action="contact.number_used").exists()
    assert AuditEvent.objects.filter(action="lead.outbound_call").exists()
    assert_no_full_number(body(response))


def test_a_lead_off_the_allowlist_is_refused_with_a_clear_message(
    env, gateway, world, settings
):
    settings.CONTACT_DIALING_ENABLED = True
    policy(env)
    lead, employee = lead_for(world), employee_for(world)
    response = call_lead(world, lead, employee)
    assert "not in OUTBOUND_ALLOWLIST_NUMBERS" in body(response)
    assert gateway.dispatches == [] and not CallRecord.objects.exists()


def test_live_mode_calls_any_consented_lead_in_campus_hours(
    env, gateway, world, settings, at_eleven
):
    settings.CONTACT_DIALING_ENABLED = True
    policy(env, "live", numbers="")
    lead, employee = lead_for(world), employee_for(world)
    call_lead(world, lead, employee)
    assert dialled(gateway) == [ASHA]

    # A closed campus refuses the next call.
    for weekday in range(7):
        OpeningHours.objects.create(
            campus=world.north, weekday=weekday, opens=time(6), closes=time(7)
        )
    calls.CallRecord.objects.update(status=CallRecord.Status.COMPLETED)
    cache.delete(f"outbound-call:{world.admin_a.pk}")  # the 5-second throttle
    second = lead_for(world, RAVI)
    response = call_lead(world, second, employee)
    assert "outside its business hours" in body(response)
    assert dialled(gateway) == [ASHA]


def test_a_lead_without_permission_to_call_is_never_dialled(
    env, gateway, world, settings, at_eleven
):
    settings.CONTACT_DIALING_ENABLED = True
    policy(env, "live", numbers="")
    lead, employee = lead_for(world), employee_for(world)
    Contact.objects.filter(pk=lead.contact_id).update(consent=Contact.Consent.UNKNOWN)
    response = call_lead(world, lead, employee)
    assert "No call was placed." in body(response)
    assert gateway.dispatches == []


def test_staff_of_another_organisation_cannot_call_the_lead(
    env, gateway, world, settings
):
    settings.CONTACT_DIALING_ENABLED = True
    policy(env, "live", numbers="")
    lead, employee = lead_for(world), employee_for(world)
    response = world.client(world.admin_b).post(
        reverse("lead-call", args=[lead.pk]), {"employee": str(employee.pk)}
    )
    assert response.status_code == 404 and gateway.dispatches == []


# --- one campaign entry ----------------------------------------------------------------


def campaign_entry(world, employee, number=ASHA, **fields):
    from .test_calling import consented

    contact = consented(world, world.north, number)
    campaign = Campaign.objects.create(
        organisation=world.org_a,
        campus=world.north,
        employee=employee,
        name="Visit reminder",
        status=fields.pop("status", Campaign.Status.READY),
        consent_attested=fields.pop("consent_attested", True),
        window_start=time(9),
        window_end=time(20),
        days=[0, 1, 2, 3, 4, 5, 6],
        **fields,
    )
    return QueuedCall.objects.create(
        organisation=world.org_a,
        campus=world.north,
        contact=contact,
        campaign=campaign,
        due_at=timezone.now() - timedelta(minutes=1),
    )


@pytest.mark.parametrize(
    ("mode", "numbers"), [("allowlist", f"{MOBILE},{ASHA}"), ("live", "")]
)
def test_one_campaign_entry_is_dispatched_once(
    env, gateway, world, settings, at_eleven, mode, numbers
):
    settings.CONTACT_DIALING_ENABLED = True
    policy(env, mode, numbers)
    entry = campaign_entry(world, employee_for(world))
    record = calling_outbound.run_campaign_entry(entry.pk, user=world.admin_a)
    assert dialled(gateway) == [ASHA]
    assert record.contact == entry.contact
    entry.refresh_from_db()
    assert (entry.status, entry.attempts) == (QueuedCall.Status.DONE, 1)
    CallRecord.objects.update(status=CallRecord.Status.COMPLETED)
    with pytest.raises(calls.CallError, match="already been dispatched"):
        calling_outbound.run_campaign_entry(entry.pk, user=world.admin_a)
    assert len(gateway.dispatches) == 1


def test_a_campaign_entry_off_the_allowlist_keeps_its_attempt(
    env, gateway, world, settings, at_eleven
):
    settings.CONTACT_DIALING_ENABLED = True
    policy(env)
    entry = campaign_entry(world, employee_for(world))
    with pytest.raises(calls.CallError, match="not in OUTBOUND_ALLOWLIST_NUMBERS"):
        calling_outbound.run_campaign_entry(entry.pk)
    entry.refresh_from_db()
    assert (entry.status, entry.attempts) == (QueuedCall.Status.QUEUED, 0)
    assert gateway.dispatches == []


@pytest.mark.parametrize(
    "fields",
    [{"status": Campaign.Status.DRAFT}, {"consent_attested": False}],
)
def test_only_ready_attested_campaigns_dispatch(
    env, gateway, world, settings, at_eleven, fields
):
    settings.CONTACT_DIALING_ENABLED = True
    policy(env, "live", numbers="")
    entry = campaign_entry(world, employee_for(world), **fields)
    with pytest.raises(calls.CallError, match="ready and consent"):
        calling_outbound.run_campaign_entry(entry.pk)
    assert gateway.dispatches == []


def test_the_campaign_command_needs_confirm(env, gateway, world, settings, at_eleven):
    from django.core.management import CommandError, call_command

    settings.CONTACT_DIALING_ENABLED = True
    policy(env, "live", numbers="")
    entry = campaign_entry(world, employee_for(world))
    with pytest.raises(CommandError, match="--confirm"):
        call_command("run_campaign_call", str(entry.pk))
    assert gateway.dispatches == []
