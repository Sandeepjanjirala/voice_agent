# ruff: noqa: F811
"""Milestone D: published employees on calls, inbound routing, campaigns,
instant leads, knowledge, call results and the actions that follow them.

LiveKit is the fake gateway from test_calls; the LLM is a canned answer; every
person and number is made up. No test dials, and no test reaches the network.
"""

import io
import json
import time
from datetime import datetime, timedelta

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.calling import analysis, inbound, intake, queue
from apps.calling.models import (
    CallAction,
    CallResult,
    CallTranscript,
    Campaign,
    IntakeEvent,
    LeadSource,
    PhoneNumber,
    QueuedCall,
)
from apps.crm import contacts, crypto, retention
from apps.crm.models import Contact, FollowUp, Lead, OptOut
from apps.telephony.models import CallRecord
from apps.tenancy.models import AuditEvent
from apps.workforce import config as employee_config
from apps.workforce import knowledge, llm, publishing
from apps.workforce.models import Employee, KnowledgeItem
from voice_agent.call_reporting import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign
from voice_agent.outbound import parse_dial_request
from voice_agent.profile import parse_profile, profile_instructions

from .test_calls import SECRET, FakeGateway, env, gateway, post_event  # noqa: F401
from .test_tenancy_crm import ASHA, LATA, RAVI, World, assert_no_full_number, body

pytestmark = pytest.mark.django_db

SCRIPT = {
    "agent_name": "Meera",
    "role": "Enquiry assistant",
    "greeting": "Hello, I am Meera, an AI assistant. Is this a good time?",
    "purpose": "Follow up on the person's enquiry.",
    "steps": [{"name": "Ask", "guidance": "Ask what they need."}],
    "questions": ["Preferred visit day"],
    "limits": "You cannot confirm anything.",
    "ending": "End politely.",
    "voice": {"speaker": "rahul", "pace": 1.1},
    "actions": ["create_follow_up", "update_lead_stage"],
}
BUSINESS_LINE = "+914012345678"


@pytest.fixture
def world():
    return World()


def employee_for(world, organisation=None, *, publish=True, **overrides) -> Employee:
    employee = Employee.objects.create(
        organisation=organisation or world.org_a,
        name="Meera",
        role="Enquiry assistant",
        language=overrides.pop("language", "en"),
        brief="Follow up on enquiries and note what people need.",
        draft=employee_config.normalize({**SCRIPT, **overrides}),
    )
    if publish:
        publishing.publish(employee, world.admin_a)
        employee.refresh_from_db()
    return employee


def consented(world, campus, number, name="Asha Rao", user=None):
    return world.contact(
        user or world.admin_a,
        campus,
        number=number,
        name=name,
        consent=Contact.Consent.GRANTED,
        consent_evidence="Ticked the box on the enquiry form.",
    )


def canned(**overrides):
    answer = {
        "summary": "The parent asked about visiting and wants a call back on Friday.",
        "outcome": "callback",
        "answers": {"preferred_visit_day": "Friday", "made_up_key": "ignored"},
        "callback_requested": True,
        "callback_when": "Friday evening",
        "opt_out_requested": False,
        **overrides,
    }

    def complete(system, user, **kwargs):
        canned.prompts.append(user)
        return answer

    canned.prompts = []
    return complete


# --- employee voice and actions -------------------------------------------------------


def test_voice_and_actions_are_edited_published_and_reach_the_worker(world):
    employee = employee_for(world, publish=False)
    admin = world.client(world.admin_a)
    url = reverse("employee-detail", args=[employee.pk])
    page = body(admin.get(url))
    assert 'name="voice_speaker"' in page and "Create a follow-up task" in page
    admin.post(
        url,
        {
            "name": "Meera",
            "role": "Enquiry assistant",
            "channel": "instant",
            "language": "te-en",
            "greeting": SCRIPT["greeting"],
            "purpose": SCRIPT["purpose"],
            "limits": SCRIPT["limits"],
            "ending": SCRIPT["ending"],
            "strictness": "3",
            "step_name": ["Ask"],
            "step_guidance": ["Ask what they need."],
            "question": ["Preferred visit day"],
            "voice_speaker": "kavya",
            "voice_pace": "0.9",
            "action": ["create_follow_up", "not_a_real_action"],
        },
    )
    employee.refresh_from_db()
    assert employee.draft["voice"] == {"speaker": "kavya", "pace": 0.9}
    assert employee.draft["actions"] == ["create_follow_up"]
    version = publishing.publish(employee, world.admin_a)
    profile = parse_profile(json.dumps(publishing.plan_call(employee).metadata))
    assert (profile.speaker, profile.pace, profile.language) == ("kavya", 0.9, "te-IN")
    assert version.config["actions"] == ["create_follow_up"]


@pytest.mark.parametrize(
    "voice, expected",
    [
        ({"speaker": "Nobody", "pace": 7}, {"speaker": "", "pace": 1.0}),
        ({"speaker": "RAHUL", "pace": "1.2"}, {"speaker": "rahul", "pace": 1.2}),
        ("junk", {"speaker": "", "pace": 1.0}),
    ],
)
def test_voice_choices_are_limited_to_what_the_worker_has(voice, expected):
    assert employee_config.normalize({"voice": voice})["voice"] == expected


def test_old_published_versions_are_not_reported_as_changed(world):
    employee = employee_for(world)
    # A version saved before voices existed has no voice or actions keys.
    old = {
        key: value
        for key, value in employee.live_version.config.items()
        if key not in ("voice", "actions")
    }
    type(employee.live_version).objects.filter(pk=employee.live_version.pk).update(
        config=old
    )
    employee.refresh_from_db()
    employee.draft = employee_config.normalize(old)
    employee.save()
    assert not publishing.has_changes(employee)


# --- the call plan and its snapshot --------------------------------------------------------


def test_a_call_uses_the_published_version_campus_knowledge_and_lead(world):
    employee = employee_for(world)
    KnowledgeItem.objects.create(
        organisation=world.org_a, title="Office hours", text="9 to 4 on weekdays."
    )
    KnowledgeItem.objects.create(
        organisation=world.org_a,
        campus=world.north,
        title="North address",
        text="Plot 1, North Road.",
    )
    KnowledgeItem.objects.create(
        organisation=world.org_a,
        campus=world.north,
        kind=KnowledgeItem.Kind.RULE,
        title="Fees",
        text="Never quote fees on the phone.",
    )
    KnowledgeItem.objects.create(
        organisation=world.org_a, campus=world.south, title="South", text="Elsewhere"
    )
    KnowledgeItem.objects.create(
        organisation=world.org_b, title="Theirs", text="Other organisation"
    )
    KnowledgeItem.objects.create(
        organisation=world.org_a, title="Old", text="Retired", is_active=False
    )
    lead = world.lead(world.admin_a, world.north, subject="Brochure request")
    # An edit after publishing must not reach the call.
    employee.draft = employee_config.normalize({**SCRIPT, "greeting": "Unpublished."})
    employee.save()

    plan = publishing.plan_call(employee, campus=world.north, lead=lead)
    profile = plan.metadata["agent_profile"]
    assert plan.version == employee.live_version
    assert profile["greeting"] == SCRIPT["greeting"]
    assert profile["business_name"] == "Org A, North"
    info = profile["business_info"]
    assert "Office hours: 9 to 4" in info and "North address" in info
    assert info.index("Facts you may state:") < info.index("Rules you must follow:")
    assert "Never quote fees" in info
    for absent in ("Elsewhere", "Other organisation", "Retired"):
        assert absent not in info
    assert profile["context"] == {"name": "Asha Rao", "about": "Brochure request"}
    # New organisations use transcripts for the summary, then delete them, and
    # ask LiveKit Cloud to keep nothing.
    assert profile["transcript"] is True and profile["observability"] is False

    snapshot = plan.snapshot
    assert snapshot["employee"]["version"] == 1 and snapshot["campus"] == "North"
    assert snapshot["lead"] == str(lead.pk) and snapshot["rehearsal"] is False
    assert snapshot["actions"] == ["create_follow_up", "update_lead_stage"]
    assert [q["key"] for q in snapshot["questions"]] == ["preferred_visit_day"]
    worker = parse_profile(json.dumps(plan.metadata))
    section = profile_instructions(worker)
    assert "You are speaking with Asha Rao." in section
    assert_no_full_number(json.dumps(snapshot))


def test_knowledge_is_cut_whole_to_the_workers_limit(world):
    for index in range(20):
        KnowledgeItem.objects.create(
            organisation=world.org_a, title=f"Item {index:02}", text="x" * 400
        )
    text = knowledge.compile_for(world.org_a)
    assert len(text) <= knowledge.LIMIT
    assert all(line.endswith("x" * 400) for line in text.splitlines()[1:])


def test_drafts_and_paused_employees_are_never_put_on_a_call(world):
    draft = employee_for(world, publish=False)
    with pytest.raises(publishing.PublishError, match="not been published"):
        publishing.plan_call(draft)
    paused = employee_for(world)
    paused.taking_calls = False
    paused.save()
    with pytest.raises(publishing.PublishError, match="paused"):
        publishing.plan_call(paused)


def test_a_calls_snapshot_cannot_be_changed(world):
    employee = employee_for(world)
    plan = publishing.plan_call(employee)
    record = CallRecord.objects.create(
        organisation=world.org_a,
        room="snap",
        masked_number="+91********10",
        status="completed",
        context_snapshot=plan.snapshot,
    )
    record = CallRecord.objects.get(pk=record.pk)
    record.notes = "fine"
    record.save()
    record.context_snapshot = {**record.context_snapshot, "profile": {}}
    with pytest.raises(ValueError, match="cannot be changed"):
        record.save()


# --- rehearsing a lead's call on the test number -------------------------------------------


def test_a_lead_is_rehearsed_on_the_test_number_only(world, gateway):
    employee = employee_for(world)
    lead = world.lead(world.admin_a, world.north, subject="Brochure request")
    admin = world.client(world.admin_a)
    page = body(admin.get(reverse("lead-detail", args=[lead.pk])))
    assert "Rehearse this call on the test number" in page and "+91********10" in page

    response = admin.post(
        reverse("lead-rehearse", args=[lead.pk]), {"employee": employee.pk}
    )
    record = CallRecord.objects.get()
    assert response["Location"] == reverse("conversation-detail", args=[record.pk])
    assert record.lead == lead and record.contact is None and record.is_rehearsal
    assert record.campus == world.north and record.employee_version.number == 1
    (dispatch,) = gateway.dispatches
    assert parse_dial_request(dispatch.metadata).to == "+919876543210"
    profile = parse_profile(dispatch.metadata)
    assert profile.caller_name == "Asha Rao" and profile.about == "Brochure request"
    assert ASHA not in dispatch.metadata, (
        "the contact's number never reaches the worker"
    )
    html = body(admin.get(reverse("conversation-detail", args=[record.pk])))
    assert "Rehearsal" in html and "Meera, version 1" in html
    assert "What the employee was given" in html


def test_rehearsals_are_for_admins_of_this_organisation(world, gateway):
    employee = employee_for(world)
    theirs = employee_for(world, world.org_b)
    lead = world.lead(world.admin_a, world.north)
    url = reverse("lead-rehearse", args=[lead.pk])
    assert (
        world.client(world.staff_north).post(url, {"employee": employee.pk}).status_code
        == 403
    )
    assert (
        world.client(world.admin_b).post(url, {"employee": theirs.pk}).status_code
        == 404
    )
    admin = world.client(world.admin_a)
    assert admin.post(url, {"employee": theirs.pk}).status_code == 404
    assert "Rehearse" not in body(
        world.client(world.staff_north).get(reverse("lead-detail", args=[lead.pk]))
    )
    assert not CallRecord.objects.exists() and gateway.dispatches == []


# --- transcripts, analysis and actions ---------------------------------------------------------


def _ended_call(world, *, lead=None, transcripts="keep", rehearsal=True, **fields):
    world.org_a.transcripts = transcripts
    world.org_a.save()
    employee = employee_for(world)
    plan = publishing.plan_call(
        employee, campus=world.north, lead=lead, rehearsal=rehearsal
    )
    return CallRecord.objects.create(
        organisation=world.org_a,
        campus=world.north,
        lead=lead,
        room=fields.pop("room", "call-1"),
        masked_number="+91********10",
        status="answered",
        answered_at=timezone.now(),
        employee_version=plan.version,
        context_snapshot=plan.snapshot,
        **fields,
    )


TRANSCRIPT = [
    {"role": "assistant", "text": "Hello, is this a good time?"},
    {"role": "user", "text": "Yes. Call me back Friday on 98765 00001."},
    {"role": "system", "text": "ignored"},
]


def test_the_transcript_is_kept_only_when_the_organisation_allows(world, env):
    record = _ended_call(world, transcripts="off")
    post_event(
        Client(),
        {
            "room": record.room,
            "event": "ended",
            "outcome": "completed",
            "transcript": TRANSCRIPT,
        },
    )
    record.refresh_from_db()
    assert record.status == "completed" and not CallTranscript.objects.exists()

    world.org_a.transcripts = "keep"
    world.org_a.save()
    response = post_event(
        Client(),
        {
            "room": record.room,
            "event": "ended",
            "outcome": "completed",
            "transcript": TRANSCRIPT,
        },
    )
    assert response.status_code == 204
    turns = CallTranscript.objects.get(call=record).turns
    assert turns[1] == {
        "role": "user",
        "text": "Yes. Call me back Friday on 98******01.",
    }
    assert turns[2]["role"] == "user", "an unknown role is never shown as the employee"
    assert CallResult.objects.get(call=record).status == "pending"


def test_analysis_summarises_and_carries_out_only_permitted_actions(world, monkeypatch):
    lead = world.lead(world.admin_a, world.north)
    record = _ended_call(world, lead=lead)
    analysis.store_transcript(record, TRANSCRIPT[:2])
    monkeypatch.setattr(llm, "complete_json", canned())
    record.status = "completed"
    record.save()

    result = analysis.analyse(record)
    assert result.status == "done" and result.outcome == "callback"
    assert result.answers == {"preferred_visit_day": "Friday"}
    assert "Preferred visit day" in canned.prompts[0]
    follow_up = FollowUp.objects.get(lead=lead)
    assert follow_up.title == "Call back Friday evening"
    lead.refresh_from_db()
    assert lead.stage == Lead.Stage.FOLLOW_UP
    assert sorted(CallAction.objects.values_list("action", "status")) == [
        ("create_follow_up", "done"),
        ("update_lead_stage", "done"),
    ]
    # Analysing again never repeats an action.
    analysis.analyse(record)
    assert FollowUp.objects.count() == 1 and CallAction.objects.count() == 2

    admin = world.client(world.admin_a)
    html = body(admin.get(reverse("conversation-detail", args=[record.pk])))
    assert "Asked for a call back" in html and "Preferred visit day" in html
    assert "Follow-up added: call back Friday evening." in html
    assert "98******01" in html and ASHA not in html


def test_actions_not_switched_on_are_logged_as_not_done(world, monkeypatch):
    lead = world.lead(world.admin_a, world.north)
    world.org_a.transcripts = "keep"
    world.org_a.save()
    employee = employee_for(world, actions=[])
    plan = publishing.plan_call(employee, campus=world.north, lead=lead)
    record = CallRecord.objects.create(
        organisation=world.org_a,
        campus=world.north,
        lead=lead,
        room="r",
        masked_number="+91********10",
        status="completed",
        employee_version=plan.version,
        context_snapshot=plan.snapshot,
    )
    CallTranscript.objects.create(call=record, turns=TRANSCRIPT[:2])
    analysis.analyse(record, complete=canned())
    assert not FollowUp.objects.exists()
    lead.refresh_from_db()
    assert lead.stage == Lead.Stage.NEW
    (action,) = CallAction.objects.all()
    assert action.status == "skipped" and "not switched on" in action.detail


def test_an_opt_out_on_a_rehearsal_is_not_applied_to_anyone(world):
    lead = world.lead(world.admin_a, world.north)
    record = _ended_call(world, lead=lead)
    CallTranscript.objects.create(call=record, turns=TRANSCRIPT[:2])
    analysis.analyse(
        record,
        complete=canned(
            outcome="opted_out", opt_out_requested=True, callback_requested=False
        ),
    )
    assert not OptOut.objects.exists()
    action = CallAction.objects.get(action="opt_out")
    assert action.status == "skipped" and "Rehearsal" in action.detail
    lead.refresh_from_db()
    assert lead.stage == Lead.Stage.LOST


def test_no_transcript_means_no_summary_and_no_invented_result(world):
    record = _ended_call(world, transcripts="off")
    result = analysis.analyse(record, complete=canned())
    assert result.status == "unavailable" and result.summary == ""
    assert "No transcript was kept" in result.error
    assert canned.prompts == [] and not CallAction.objects.exists()


def test_analyse_mode_deletes_the_transcript_after_use(world):
    record = _ended_call(world, transcripts="analyse")
    CallTranscript.objects.create(call=record, turns=TRANSCRIPT[:2])
    analysis.analyse(record, complete=canned(callback_requested=False))
    assert not CallTranscript.objects.exists()
    assert CallResult.objects.get(call=record).status == "done"


def test_a_failed_analysis_is_reported_not_guessed(world):
    record = _ended_call(world)
    CallTranscript.objects.create(call=record, turns=TRANSCRIPT[:2])

    def down(*args, **kwargs):
        raise llm.LLMError("The LLM service answered HTTP 503.")

    result = analysis.analyse(record, complete=down)
    assert result.status == "failed" and "503" in result.error
    assert result.outcome == "" and not CallAction.objects.exists()


def test_analyse_now_and_the_scheduled_command(world, monkeypatch):
    record = _ended_call(world)
    record.status = "completed"
    record.save()
    CallTranscript.objects.create(call=record, turns=TRANSCRIPT[:2])
    CallResult.objects.create(call=record)
    monkeypatch.setattr(llm, "complete_json", canned(callback_requested=False))
    out = io.StringIO()
    call_command("analyse_calls", stdout=out)
    assert "1 calls are waiting" in out.getvalue()
    assert CallResult.objects.get(call=record).status == "pending"
    call_command("analyse_calls", "--apply", stdout=out)
    assert CallResult.objects.get(call=record).status == "done"

    staff = world.client(world.staff_north)
    response = staff.post(
        reverse("conversation-detail", args=[record.pk]),
        {"action": "analyse"},
        follow=True,
    )
    assert "The call was analysed." in body(response)
    assert (
        world.client(world.admin_b)
        .post(reverse("conversation-detail", args=[record.pk]), {"action": "analyse"})
        .status_code
        == 404
    )


def test_conversations_filter_by_employee_and_outcome(world):
    record = _ended_call(world)
    CallResult.objects.create(call=record, status="done", outcome="interested")
    world.call(world.org_a, world.north, room="other")
    admin = world.client(world.admin_a)
    employee = record.employee_version.employee

    def total(**params):
        return admin.get(reverse("conversations"), params).context["summary"]["total"]

    assert total() == 2
    assert total(employee=employee.pk) == 1 and total(outcome="interested") == 1
    assert total(outcome="callback") == 0 and total(employee="nonsense") == 2


# --- inbound routing ----------------------------------------------------------------------------


def route(world, employee, campus=None, **fields):
    return PhoneNumber.objects.create(
        organisation=world.org_a,
        campus=campus,
        number=BUSINESS_LINE,
        employee=employee,
        routing_enabled=True,
        **fields,
    )


def inbound_post(payload, *, secret=SECRET):
    body_ = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    return Client().post(
        reverse("telephony:inbound-context"),
        data=body_,
        content_type="application/json",
        **{
            "HTTP_" + TIMESTAMP_HEADER.upper().replace("-", "_"): timestamp,
            "HTTP_" + SIGNATURE_HEADER.upper().replace("-", "_"): sign(
                secret, timestamp, body_
            ),
        },
    )


def test_an_inbound_call_is_answered_by_the_routed_published_employee(
    world, env, caplog
):
    world.org_a.transcripts = "keep"
    world.org_a.save()
    employee = employee_for(world)
    route(world, employee, campus=world.north)
    contact = consented(world, world.north, ASHA)
    lead = world.lead(world.admin_a, world.north, subject="Admission enquiry")
    payload = {"room": "in-1", "called": "040 1234 5678", "caller": ASHA}

    # The dialled business line may arrive in any common format.
    response = inbound_post({**payload, "called": "040 1234 5678"})
    assert response.status_code == 200
    profile = parse_profile(json.dumps(response.json()))
    assert profile.greeting == SCRIPT["greeting"] and profile.caller_name == "Asha Rao"
    assert profile.keep_transcript is True and profile.speaker == "rahul"
    record = CallRecord.objects.get(room="in-1")
    assert record.direction == "inbound" and record.organisation == world.org_a
    assert record.contact == contact and record.lead == lead
    assert record.campus == world.north and record.employee_version.number == 1
    assert record.masked_number == "+91********01"
    assert crypto.decrypt(record.remote_encrypted) == ASHA
    assert record.context_snapshot["number"] == BUSINESS_LINE
    assert ASHA not in response.content.decode() and ASHA not in caplog.text

    # The worker asking again for the same room gets the same answer.
    again = inbound_post({**payload, "called": BUSINESS_LINE})
    assert again.json() == response.json() and CallRecord.objects.count() == 1


@pytest.mark.parametrize(
    "problem", ["unrouted", "switched_off", "paused", "draft", "other_org"]
)
def test_inbound_calls_are_routed_only_to_ready_employees(world, env, problem):
    employee = employee_for(world, publish=problem != "draft")
    if problem == "other_org":
        employee = employee_for(world, world.org_b)
    if problem == "paused":
        employee.taking_calls = False
        employee.save()
    if problem != "unrouted":
        route(world, employee)
    if problem == "switched_off":
        PhoneNumber.objects.update(routing_enabled=False)
    response = inbound_post({"room": "in-2", "called": BUSINESS_LINE, "caller": RAVI})
    assert response.status_code == 404
    if problem == "unrouted":
        # Not one of our numbers: nothing to record.
        assert not CallRecord.objects.exists()
        return
    # One of our numbers that no ready employee could take: someone calls back.
    record = CallRecord.objects.get()
    assert record.status == "failed" and record.employee_version is None
    assert record.organisation == world.org_a and record.failure_reason
    assert record.missed.reason == "not_routed"
    assert crypto.decrypt(record.remote_encrypted) == RAVI


def test_the_inbound_endpoint_needs_a_valid_signature(world, env):
    route(world, employee_for(world))
    payload = {"room": "in-3", "called": BUSINESS_LINE, "caller": RAVI}
    assert inbound_post(payload, secret="wrong" * 10).status_code == 403
    assert (
        Client()
        .post(
            reverse("telephony:inbound-context"),
            data=b"{}",
            content_type="application/json",
        )
        .status_code
        == 403
    )
    assert not CallRecord.objects.exists()


def test_an_unknown_caller_can_become_a_lead_without_showing_the_number(world, env):
    route(world, employee_for(world), campus=world.south)
    inbound_post({"room": "in-4", "called": BUSINESS_LINE, "caller": LATA})
    record = CallRecord.objects.get()
    assert record.contact is None and record.campus == world.south
    admin = world.client(world.admin_a)
    url = reverse("conversation-detail", args=[record.pk])
    page = body(admin.get(url))
    assert "Unknown caller" in page and "Add as a lead" in page
    assert LATA not in page and LATA[3:] not in page
    response = admin.post(
        url, {"action": "create_lead", "name": "Lata Devi", "subject": "Transport"}
    )
    lead = Lead.objects.get()
    assert response["Location"] == reverse("lead-detail", args=[lead.pk])
    contact = lead.contact
    assert contact.campus == world.south and contact.origin == "phone"
    assert crypto.decrypt(contact.phone_encrypted) == LATA
    assert contact.consent == Contact.Consent.UNKNOWN, "calling them back needs consent"
    record.refresh_from_db()
    assert record.contact == contact and record.lead == lead
    # Campus staff of another campus cannot do it.
    assert (
        world.client(world.staff_north)
        .post(url, {"action": "create_lead", "name": "X", "subject": "Y"})
        .status_code
        == 404
    )


def test_an_inbound_caller_who_asks_is_added_to_do_not_call(world, env):
    world.org_a.transcripts = "keep"
    world.org_a.save()
    route(world, employee_for(world))
    inbound_post({"room": "in-5", "called": BUSINESS_LINE, "caller": RAVI})
    record = CallRecord.objects.get()
    record.status = "completed"
    record.save()
    CallTranscript.objects.create(call=record, turns=TRANSCRIPT[:2])
    analysis.analyse(
        record,
        complete=canned(
            outcome="opted_out", opt_out_requested=True, callback_requested=False
        ),
    )
    assert contacts.is_opted_out(world.org_a, crypto.lookup(RAVI))
    assert CallAction.objects.get(action="opt_out").status == "done"
    assert AuditEvent.objects.filter(action="optout.added").exists()


# --- numbers, knowledge and their pages -----------------------------------------------------------


def test_numbers_are_managed_by_admins_within_their_organisation(world):
    employee = employee_for(world)
    theirs = employee_for(world, world.org_b)
    admin = world.client(world.admin_a)
    data = {
        "number": "+91 40 1234 5678",
        "label": "Front desk",
        "campus": world.north.pk,
        "employee": employee.pk,
        "routing_enabled": "on",
        "transfer_to": "",
    }
    assert admin.post(reverse("numbers"), data).status_code == 302
    number = PhoneNumber.objects.get()
    assert number.number == BUSINESS_LINE and number.organisation == world.org_a
    assert inbound.route_for(BUSINESS_LINE) == number
    page = body(admin.get(reverse("numbers")))
    assert "Routed" in page and "Inbound readiness" in page
    for change, message in (
        ({}, "already set up"),
        ({"number": "+91 40 5555 0000", "employee": theirs.pk}, "not one of the"),
        ({"number": "+91 40 5555 0000", "campus": world.west.pk}, "not one of the"),
        ({"number": "12"}, "with its area code"),
    ):
        response = admin.post(reverse("numbers"), {**data, **change})
        assert response.status_code == 200 and message in body(response), change
    staff = world.client(world.staff_north)
    assert staff.get(reverse("numbers")).status_code == 200
    assert staff.post(reverse("numbers"), data).status_code == 403
    assert staff.get(reverse("number-edit", args=[number.pk])).status_code == 403
    assert (
        world.client(world.admin_b)
        .get(reverse("number-edit", args=[number.pk]))
        .status_code
        == 404
    )
    admin.post(reverse("number-edit", args=[number.pk]), {"action": "delete"})
    assert not PhoneNumber.objects.exists()


def test_knowledge_is_managed_per_campus_and_previewed(world):
    admin = world.client(world.admin_a)
    data = {
        "kind": "fact",
        "campus": world.north.pk,
        "title": "Bus",
        "text": "Route 5.",
        "is_active": "on",
    }
    assert admin.post(reverse("knowledge"), data).status_code == 302
    item = KnowledgeItem.objects.get()
    page = body(admin.get(reverse("knowledge"), {"campus": world.north.pk}))
    assert "Bus: Route 5." in page
    # Organisation-wide calls are not given a campus's items.
    assert admin.get(reverse("knowledge")).context["preview"] == ""
    assert (
        admin.post(reverse("knowledge"), {**data, "campus": world.west.pk}).status_code
        == 200
    )
    staff = world.client(world.staff_north)
    assert staff.get(reverse("knowledge")).status_code == 200
    assert staff.post(reverse("knowledge"), data).status_code == 403
    assert (
        world.client(world.admin_b)
        .get(reverse("knowledge-edit", args=[item.pk]))
        .status_code
        == 404
    )
    admin.post(reverse("knowledge-edit", args=[item.pk]), {"action": "delete"})
    assert not KnowledgeItem.objects.exists()


NEW_PAGES = ["numbers", "inbound", "campaigns", "instant", "knowledge"]


@pytest.mark.parametrize("name", NEW_PAGES)
def test_new_pages_are_real_and_scoped(world, name, client):
    url = reverse(name)
    assert client.get(url).status_code == 302
    assert world.client(world.outsider).get(url).status_code == 403
    for user in (world.admin_a, world.staff_north):
        response = world.client(user).get(url)
        assert response.status_code == 200
        assert "Not built yet" not in body(response)
    sidebar = body(world.client(world.admin_a).get(reverse("home"))).split("<main>")[0]
    assert "Planned" not in sidebar or 'class="soon"' not in sidebar


# --- campaigns -------------------------------------------------------------------------------------


def campaign_for(world, employee, **fields):
    return Campaign.objects.create(
        organisation=world.org_a,
        campus=world.north,
        employee=employee,
        name="Reminder",
        purpose="Remind parents about a pending document",
        days=[0, 1, 2, 3, 4, 5, 6],
        window_start="09:00",
        window_end="21:00",
        **fields,
    )


def test_creating_a_campaign_enforces_hours_and_published_employees(world):
    employee = employee_for(world)
    draft = employee_for(world, publish=False)
    admin = world.client(world.admin_a)
    data = {
        "name": "Document reminder",
        "campus": world.north.pk,
        "employee": employee.pk,
        "purpose": "Remind parents about a pending document",
        "window_start": "10:00",
        "window_end": "18:00",
        "days": ["0", "1", "2"],
        "max_attempts": "2",
        "retry_after_minutes": "120",
        "daily_limit": "50",
        "cooldown_days": "7",
    }
    for change, message in (
        ({"window_start": "07:00"}, "only between 09:00 and 21:00"),
        ({"window_end": "09:30", "window_start": "10:00"}, "must end after"),
        ({"employee": draft.pk}, "not one of the"),
        ({"campus": world.west.pk}, "not one of the"),
        ({"max_attempts": "9"}, "Between 1 and 5"),
    ):
        response = admin.post(reverse("campaign-new"), {**data, **change})
        assert response.status_code == 200 and message in body(response), change
    response = admin.post(reverse("campaign-new"), data)
    campaign = Campaign.objects.get()
    assert response["Location"] == reverse("campaign-detail", args=[campaign.pk])
    assert campaign.days == [0, 1, 2] and campaign.organisation == world.org_a
    assert (
        world.client(world.staff_north).get(reverse("campaign-new")).status_code == 403
    )


def test_a_dry_run_checks_every_rule_and_dials_nobody(world, gateway):
    employee = employee_for(world)
    campaign = campaign_for(world, employee, max_attempts=2)
    ok = consented(world, world.north, ASHA)
    no_consent = world.contact(world.admin_a, world.north, number=RAVI, name="Ravi")
    opted = consented(world, world.north, LATA, name="Lata")
    contacts.opt_out_number(world.org_a, LATA)
    erased = consented(world, world.north, "+919876500004", name="Gone")
    spent = consented(world, world.north, "+919876500005", name="Spent")
    for contact in (ok, no_consent, opted, erased, spent):
        QueuedCall.objects.create(
            organisation=world.org_a,
            campus=world.north,
            campaign=campaign,
            contact=contact,
        )
    contacts.erase(erased)
    QueuedCall.objects.filter(contact=spent).update(attempts=2)

    admin = world.client(world.admin_a)
    url = reverse("campaign-detail", args=[campaign.pk])
    response = admin.post(url, {"action": "dry_run"}, follow=True)
    assert "Record that these people agreed" in body(response)
    admin.post(
        url,
        {
            "action": "attest",
            "consent-attested": "on",
            "consent-note": "Enquiry forms, November",
        },
    )
    campaign.refresh_from_db()
    assert campaign.status == "ready" and campaign.consent_by == world.admin_a
    response = admin.post(url, {"action": "dry_run"}, follow=True)
    assert "1 would be called, 4 skipped. Nobody was called." in body(response)

    status = dict(QueuedCall.objects.values_list("contact__name", "status"))
    assert status["Asha Rao"] == "would_call"
    reasons = dict(QueuedCall.objects.values_list("contact__name", "reason"))
    assert "Permission to call" in reasons["Ravi"]
    assert "do-not-call" in reasons["Lata"]
    assert "erased" in reasons["Erased contact"]
    assert reasons["Spent"] == "No attempts left."
    run = campaign.runs.get()
    assert run.summary["would_call"] == 1 and run.summary["dialling_enabled"] is False
    assert len(run.summary["planned"][0]["attempts"]) == 2
    page = body(admin.get(url))
    assert "Real calling is switched off" in page and "Last dry run" in page
    assert_no_full_number(page)
    # Nothing was dialled, recorded as a call, or charged an attempt.
    assert gateway.dispatches == [] and not CallRecord.objects.exists()
    assert QueuedCall.objects.get(contact=ok).attempts == 0


def test_daily_limit_hours_and_cooldown(world):
    employee = employee_for(world)
    campaign = campaign_for(world, employee, daily_limit=1, cooldown_days=7)
    other = campaign_for(world, employee)
    first = consented(world, world.north, ASHA)
    second = consented(world, world.north, RAVI, name="Ravi")
    for contact in (first, second):
        QueuedCall.objects.create(
            organisation=world.org_a,
            campus=world.north,
            campaign=campaign,
            contact=contact,
        )
    QueuedCall.objects.create(
        organisation=world.org_a,
        campus=world.north,
        campaign=other,
        contact=second,
        status="would_call",
        checked_at=timezone.now() - timedelta(days=2),
    )
    queue.dry_run(
        campaign.queue.all(),
        queue.Rules.for_campaign(campaign),
        employee,
        campaign=campaign,
    )
    entries = {e.contact.name: e for e in campaign.queue.select_related("contact")}
    assert entries["Asha Rao"].status == "would_call"
    assert entries["Ravi"].status == "skipped"
    assert "another campaign recently" in entries["Ravi"].reason

    rules = queue.Rules.for_campaign(campaign)
    late = timezone.make_aware(datetime(2026, 11, 18, 22, 30))
    slot = timezone.localtime(queue.next_slot(rules, late))
    assert (slot.date().day, slot.hour) == (19, 9)
    # The widest hours apply whatever a campaign says.
    campaign.window_start, campaign.window_end = "06:00", "23:00"
    assert queue.Rules.for_campaign(campaign).window_start.hour == 9
    assert queue.Rules.for_campaign(campaign).window_end.hour == 21


def test_a_second_person_over_the_daily_limit_moves_to_the_next_day(world):
    employee = employee_for(world)
    campaign = campaign_for(world, employee, daily_limit=1, cooldown_days=0)
    for number, name in ((ASHA, "A"), (RAVI, "B")):
        QueuedCall.objects.create(
            organisation=world.org_a,
            campus=world.north,
            campaign=campaign,
            contact=consented(world, world.north, number, name=name),
        )
    queue.dry_run(
        campaign.queue.all(),
        queue.Rules.for_campaign(campaign),
        employee,
        campaign=campaign,
    )
    days = sorted(timezone.localtime(e.due_at).date() for e in campaign.queue.all())
    assert days[1] > days[0]


def test_contacts_are_added_from_the_campus_or_an_import(world):
    employee = employee_for(world)
    campaign = campaign_for(world, employee)
    consented(world, world.north, ASHA)
    world.contact(world.admin_a, world.north, number=RAVI, name="No consent")
    consented(world, world.south, LATA, name="Other campus")
    admin = world.client(world.admin_a)
    url = reverse("campaign-detail", args=[campaign.pk])
    response = admin.post(
        url, {"action": "add", "add-source": "consented"}, follow=True
    )
    assert "1 contacts added; 0 were already in the campaign." in body(response)
    response = admin.post(
        url, {"action": "add", "add-source": "consented"}, follow=True
    )
    assert "0 contacts added; 1 were already" in body(response)
    assert campaign.queue.count() == 1
    response = admin.post(url, {"action": "add", "add-source": "import"})
    assert "Choose an import." in body(response)
    staff = world.client(world.staff_north)
    assert staff.get(url).status_code == 200
    assert (
        staff.post(url, {"action": "add", "add-source": "consented"}).status_code == 403
    )
    south = Campaign.objects.create(
        organisation=world.org_a,
        campus=world.south,
        employee=employee,
        name="S",
        purpose="p",
        days=[0],
    )
    assert staff.get(reverse("campaign-detail", args=[south.pk])).status_code == 404
    assert world.client(world.admin_b).get(url).status_code == 404


# --- instant leads -------------------------------------------------------------------------------


def make_source(world, employee=None, **fields):
    secret, digest, hint = intake.new_secret()
    source = LeadSource.objects.create(
        organisation=world.org_a,
        campus=world.north,
        employee=employee,
        name="Website form",
        consent_statement="By submitting you agree that we may call you about it.",
        secret_hash=digest,
        secret_hint=hint,
        **fields,
    )
    return source, secret


def deliver(source, secret, payload):
    return Client().post(
        reverse("lead-intake", args=[source.pk]),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_LEAD_SECRET=secret,
    )


def test_a_new_source_shows_its_secret_once_and_stores_only_a_hash(world):
    employee = employee_for(world)
    admin = world.client(world.admin_a)
    response = admin.post(
        reverse("source-new"),
        {
            "name": "Website form",
            "campus": world.north.pk,
            "employee": employee.pk,
            "consent_statement": "By submitting you agree that we may call you.",
            "call_delay_minutes": "5",
            "window_start": "09:00",
            "window_end": "20:00",
            "is_active": "on",
        },
        follow=True,
    )
    html = body(response)
    source = LeadSource.objects.get()
    shown = html.split("<code>")[1].split("</code>")[0]
    assert shown.startswith("ls_") and source.secret_hash == intake.hash_secret(shown)
    assert shown not in json.dumps(list(LeadSource.objects.values()), default=str)
    assert shown not in body(admin.get(reverse("source-detail", args=[source.pk])))
    assert deliver(source, shown, {"phone": ASHA}).status_code == 201


def test_intake_creates_a_consented_contact_lead_and_queued_call(world, caplog):
    employee = employee_for(world)
    source, secret = make_source(world, employee)
    response = deliver(
        source,
        secret,
        {
            "phone": "98765 00001",
            "name": "Asha Rao",
            "subject": "Visit",
            "extra": "ignored",
        },
    )
    assert response.status_code == 201 and response.json()["result"] == "accepted"
    lead = Lead.objects.get()
    contact = lead.contact
    assert contact.consent == "granted" and "Website form" in contact.consent_evidence
    assert "agree that we may call" in contact.consent_evidence
    assert lead.employee == employee and lead.subject == "Visit"
    entry = QueuedCall.objects.get()
    assert entry.source == source and entry.lead == lead
    event = IntakeEvent.objects.get()
    assert event.masked_number == "+91********01"
    assert ASHA not in response.content.decode() and ASHA not in caplog.text
    assert AuditEvent.objects.get(action="contact.created").actor_name.startswith(
        "lead source"
    )

    again = deliver(source, secret, {"phone": ASHA})
    assert again.json()["result"] == "duplicate" and Lead.objects.count() == 1


@pytest.mark.parametrize(
    "secret, payload, status",
    [
        ("wrong", {"phone": "9876500001"}, 401),
        (None, {"phone": "12345"}, 400),
        (None, ["not", "an", "object"], 400),
        (None, {}, 400),
    ],
)
def test_intake_refuses_bad_deliveries(world, secret, payload, status):
    source, real = make_source(world)
    response = deliver(source, secret or real, payload)
    assert response.status_code == status
    assert not Lead.objects.exists() and not Contact.objects.exists()


def test_intake_honours_do_not_call_tests_and_switched_off_sources(world):
    source, secret = make_source(world)
    contacts.opt_out_number(world.org_a, ASHA)
    response = deliver(source, secret, {"phone": ASHA})
    assert response.json()["result"] == "opted_out" and not Lead.objects.exists()
    test = deliver(source, secret, {"phone": RAVI, "dry_run": True})
    assert test.json()["result"] == "test" and not Contact.objects.exists()
    source.is_active = False
    source.save()
    assert deliver(source, secret, {"phone": RAVI}).status_code == 401
    _other, other_secret = make_source(world)
    assert deliver(source, other_secret, {"phone": RAVI}).status_code == 401


def test_intake_is_rate_limited(world):
    cache.clear()
    source, secret = make_source(world)
    from apps.calling import views

    limit = views.INTAKE_RATE_PER_MINUTE
    codes = [
        deliver(source, secret, {"phone": RAVI, "dry_run": True}).status_code
        for _ in range(limit + 1)
    ]
    assert codes[-1] == 429 and codes[0] == 200


def test_source_pages_test_delivery_and_dry_run(world, gateway):
    employee = employee_for(world)
    source, secret = make_source(world, employee)
    deliver(source, secret, {"phone": ASHA, "name": "Asha Rao"})
    admin = world.client(world.admin_a)
    url = reverse("source-detail", args=[source.pk])
    response = admin.post(url, {"action": "test"}, follow=True)
    assert "Test delivery: Test (nothing stored)." in body(response)
    response = admin.post(url, {"action": "dry_run"}, follow=True)
    assert "1 would be called" in body(response)
    assert gateway.dispatches == [] and not CallRecord.objects.exists()
    page = body(admin.get(url))
    assert "X-Lead-Secret" in page and secret not in page
    assert (
        world.client(world.staff_north).post(url, {"action": "dry_run"}).status_code
        == 403
    )
    assert world.client(world.admin_b).get(url).status_code == 404
    response = admin.post(url, {"action": "new_secret"}, follow=True)
    assert deliver(source, secret, {"phone": RAVI}).status_code == 401


# --- retention ------------------------------------------------------------------------------------


def test_old_transcripts_are_deleted_by_the_retention_policy(world):
    record = _ended_call(world)
    old = CallTranscript.objects.create(call=record, turns=TRANSCRIPT[:2])
    CallTranscript.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(days=40)
    )
    policy = retention.policy_for(world.org_a)
    assert policy.transcript_days == 30
    assert retention.preview(world.org_a)["transcripts"] == 1
    retention.apply(world.org_a)
    assert not CallTranscript.objects.exists()
    assert CallRecord.objects.filter(pk=record.pk).exists()


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("040 1234 5678", "+914012345678"),
        ("+91 40 1234 5678", "+914012345678"),
        ("4012345678", "+914012345678"),
        ("98765 00001", "+919876500001"),
        ("+1 415 555 0100", "+14155550100"),
    ],
)
def test_business_lines_and_callers_may_be_landlines(raw, expected):
    from apps.crm.phones import normalize_line

    assert normalize_line(raw) == expected


@pytest.mark.parametrize("raw", ["", "123", "0123456789", "+910123456789", "call"])
def test_bad_lines_are_refused(raw):
    from apps.crm.phones import InvalidPhoneError, normalize_line

    with pytest.raises(InvalidPhoneError):
        normalize_line(raw)


def test_key_fingerprints_identify_keys_without_revealing_them(settings):
    out = io.StringIO()
    call_command("contactkeys", "fingerprint", stdout=out)
    text = out.getvalue()
    assert "Encryption key 1 (encrypts and decrypts)" in text and "Lookup key" in text
    assert settings.CONTACT_ENCRYPTION_KEYS[0] not in text
    assert settings.CONTACT_LOOKUP_KEY not in text
    again = io.StringIO()
    call_command("contactkeys", "fingerprint", stdout=again)
    assert again.getvalue() == text


def test_new_organisations_analyse_then_delete_and_opt_out_of_cloud_recording(world):
    from apps.tenancy.models import Organisation

    fresh = Organisation.objects.create(name="Fresh")
    assert fresh.transcripts == "analyse" and fresh.cloud_observability is False
    world.org_a.cloud_observability = True
    world.org_a.save()
    plan = publishing.plan_call(employee_for(world))
    worker = parse_profile(json.dumps(plan.metadata))
    assert worker.cloud_recording is True
