"""Inbound calling, simulated end to end: several business lines on one
secure trunk, each routed to its own campus and published employee, and a
whole SIP call played through Django's signed endpoints as the voice worker
would. No LiveKit, no provider, no real call."""

# The fixtures imported from other test modules are named as test parameters.
# ruff: noqa: F811

import json
from datetime import time

import pytest
from django.test import Client

from apps.calling import analysis, inbound
from apps.calling.models import CallResult, MissedCall, PhoneNumber
from apps.crm.models import FollowUp
from apps.telephony import providers
from apps.telephony.conf import load_telephony_settings
from apps.telephony.models import CallRecord
from apps.telephony.planning import (
    build_plan,
    dispatch_rule_request,
    inbound_trunk_request,
)
from apps.tenancy.models import OpeningHours
from voice_agent.profile import parse_profile

from .test_calling import (
    SCRIPT,
    TRANSCRIPT,
    canned,
    consented,
    employee_for,
    inbound_post,
)
from .test_calls import env, post_event  # noqa: F401
from .test_tenancy_crm import ASHA, RAVI, World

pytestmark = pytest.mark.django_db

NORTH_LINE = "+914012345671"
SOUTH_LINE = "+914012345672"


@pytest.fixture
def world(env):
    return World()


def line(world, number, campus, employee, **fields):
    return PhoneNumber.objects.create(
        organisation=campus.organisation,
        campus=campus,
        number=number,
        employee=employee,
        routing_enabled=True,
        **fields,
    )


# --- the trunk ---------------------------------------------------------------------------


READY = {
    "TELEPHONY_PROVIDER": "vobiz",
    "LIVEKIT_SIP_SUBDOMAIN": "abc123xyz",
    "SIP_INBOUND_AUTH_MODE": "ip_allowlist",
    "SIP_INBOUND_ALLOWED_ADDRESSES": "13.233.44.61,3.111.255.163",
    "SIP_INBOUND_MEDIA_ENCRYPTION": "require",
}


def test_one_trunk_carries_every_routed_line_and_nothing_else(world):
    north, south = employee_for(world), employee_for(world, name="Meera South")
    line(world, NORTH_LINE, world.north, north)
    line(world, SOUTH_LINE, world.south, south)
    PhoneNumber.objects.create(
        organisation=world.org_a, number="+914012345673"
    )  # not routed
    settings = load_telephony_settings(
        {**READY, "TELEPHONY_INBOUND_NUMBER": "+914012345670"}
    )
    plan = build_plan(
        settings, agent_name="telugu-agent", extra_numbers=inbound.trunk_numbers()
    )
    assert plan.trunk.all_numbers == ("+914012345670", NORTH_LINE, SOUTH_LINE)
    assert "+914012345673" not in plan.trunk.all_numbers


def test_the_trunk_stays_blocked_until_the_allow_list_is_enforced(world):
    line(world, NORTH_LINE, world.north, employee_for(world))
    settings = load_telephony_settings(
        {
            **READY,
            "TELEPHONY_CONFIRMED": f"{providers.ACCOUNT_READY},{providers.REGIONAL_ENDPOINT},{providers.INBOUND_SRTP}",
        }
    )
    plan = build_plan(
        settings, agent_name="telugu-agent", extra_numbers=inbound.trunk_numbers()
    )
    assert providers.ALLOWED_ADDRESSES_ENFORCED in {b.code for b in plan.blockers}
    assert not plan.ready


def test_the_ready_trunk_and_rule_are_authenticated_isolated_and_explicit(world):
    line(world, NORTH_LINE, world.north, employee_for(world))
    everything = ",".join(
        (
            providers.ACCOUNT_READY,
            providers.REGIONAL_ENDPOINT,
            providers.INBOUND_SRTP,
            providers.ALLOWED_ADDRESSES_ENFORCED,
        )
    )
    settings = load_telephony_settings({**READY, "TELEPHONY_CONFIRMED": everything})
    plan = build_plan(
        settings, agent_name="telugu-agent", extra_numbers=inbound.trunk_numbers()
    )
    assert plan.ready, [b.code for b in plan.blockers]
    trunk = inbound_trunk_request(plan, settings).trunk
    assert list(trunk.numbers) == [NORTH_LINE]
    assert sorted(trunk.allowed_addresses) == ["13.233.44.61", "3.111.255.163"]
    assert not trunk.auth_password, "no shared password with the allow-list"
    rule = dispatch_rule_request(plan, trunk_id="ST_in")
    assert list(rule.trunk_ids) == ["ST_in"], "never a wildcard rule"
    assert rule.rule.dispatch_rule_callee.randomize, "one room per call"
    assert [a.agent_name for a in rule.room_config.agents] == ["telugu-agent"]
    assert ".india.sip.livekit.cloud" in plan.sip_endpoint


# --- a whole simulated call ------------------------------------------------------------


def test_a_simulated_sip_call_from_ring_to_result(world, settings):
    world.org_a.transcripts = "keep"
    world.org_a.save()
    north, south = (
        employee_for(world),
        employee_for(world, name="Meera South", greeting="South line"),
    )
    line(world, NORTH_LINE, world.north, north)
    line(world, SOUTH_LINE, world.south, south)
    contact = consented(world, world.north, ASHA)
    lead = world.lead(world.admin_a, world.north, subject="Admission enquiry")

    # 1. The worker asks Django who answers, signed, as voice_agent/inbound.py does.
    response = inbound_post({"room": "call-n1", "called": NORTH_LINE, "caller": ASHA})
    assert response.status_code == 200
    profile = parse_profile(json.dumps(response.json()))
    assert profile.greeting == SCRIPT["greeting"] and profile.caller_name == "Asha Rao"
    record = CallRecord.objects.get(room="call-n1")
    assert (record.campus, record.contact, record.lead) == (world.north, contact, lead)
    assert record.employee_version == north.live_version

    # The other line goes to its own campus and employee, never this one.
    south_call = inbound_post({"room": "call-s1", "called": SOUTH_LINE, "caller": RAVI})
    assert parse_profile(json.dumps(south_call.json())).greeting == "South line"
    assert CallRecord.objects.get(room="call-s1").campus == world.south

    # 2. Answered, then ended with the transcript, as the worker reports.
    assert (
        post_event(Client(), {"room": "call-n1", "event": "answered"}).status_code
        == 204
    )
    ended = post_event(
        Client(),
        {
            "room": "call-n1",
            "event": "ended",
            "outcome": "completed",
            "talk_seconds": 61,
            "transcript": TRANSCRIPT,
        },
    )
    assert ended.status_code == 204
    record.refresh_from_db()
    assert record.status == "completed" and record.duration_seconds == 61

    # 3. The analysis writes the result and carries out the permitted follow-up.
    analysis.analyse(record, complete=canned())
    result = CallResult.objects.get(call=record)
    assert result.status == "done" and result.outcome == "callback"
    assert FollowUp.objects.filter(lead=lead).exists()


def test_after_hours_the_employee_takes_a_message_and_a_call_back_is_listed(world):
    line(world, NORTH_LINE, world.north, employee_for(world))
    for weekday in range(7):
        OpeningHours.objects.create(
            campus=world.north, weekday=weekday, opens=time(0, 0), closes=time(0, 1)
        )
    world.north.after_hours_message = "Office hours are 9 to 5."
    world.north.save()
    response = inbound_post({"room": "call-n2", "called": NORTH_LINE, "caller": RAVI})
    profile = parse_profile(json.dumps(response.json()))
    assert (
        "closed now" in profile.notice and "Office hours are 9 to 5." in profile.notice
    )
    assert MissedCall.objects.get().reason == "after_hours"


def test_another_organisations_line_never_routes_here(world):
    other = employee_for(world, world.org_b)
    PhoneNumber.objects.create(
        organisation=world.org_b,
        campus=world.west,
        number=NORTH_LINE,
        employee=other,
        routing_enabled=True,
    )
    response = inbound_post({"room": "call-x", "called": NORTH_LINE, "caller": ASHA})
    record = CallRecord.objects.get(room="call-x")
    assert record.organisation == world.org_b and record.campus == world.west
    assert parse_profile(json.dumps(response.json())) is not None
    # A number no organisation owns is not answered, and nothing is recorded.
    assert (
        inbound_post(
            {"room": "call-y", "called": "+914099999999", "caller": ASHA}
        ).status_code
        == 404
    )
    assert not CallRecord.objects.filter(room="call-y").exists()
