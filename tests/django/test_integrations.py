"""Milestone E: integrations, approved actions, webhooks, opening hours and
background jobs. No test reaches the network: the transport is a fake that
records requests, and live sending is off unless a test turns it on."""

import hashlib
import hmac
import json
from datetime import datetime, time, timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.calling import analysis
from apps.calling.models import CallResult, CallTranscript, QueuedCall
from apps.crm import contacts
from apps.crm.models import Contact, FollowUp, Lead
from apps.integrations import actions, safety, schema, secrets, transport, webhooks
from apps.integrations.models import (
    ActionRun,
    Appointment,
    ApprovedAction,
    Integration,
    WebhookDelivery,
)
from apps.operations import jobs
from apps.operations.models import JobRun, Notification, UsageDay
from apps.telephony.models import CallRecord
from apps.tenancy import hours
from apps.tenancy.models import Holiday, OpeningHours
from apps.workforce import config as employee_config
from apps.workforce import publishing
from apps.workforce.models import Employee

from .test_tenancy_crm import ASHA, World, assert_no_full_number

pytestmark = pytest.mark.django_db

Kind = ApprovedAction.Kind


@pytest.fixture
def world():
    cache.clear()
    return World()


class FakeTransport:
    """Records every request; answers from a queue of (status, body)."""

    def __init__(self, monkeypatch, *answers):
        self.sent = []
        self.answers = list(answers)
        monkeypatch.setattr(transport, "send", self)

    def __call__(
        self, method, url, *, headers=None, body=None, timeout=8.0, max_bytes=None
    ):
        self.sent.append(
            {"method": method, "url": url, "headers": headers or {}, "body": body}
        )
        status, data = self.answers.pop(0) if self.answers else (200, {})
        payload = data if isinstance(data, bytes) else json.dumps(data).encode()
        return transport.Response(status, payload)


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to reach the network")

    monkeypatch.setattr(transport, "send", refuse)


@pytest.fixture
def live(settings):
    settings.INTEGRATIONS_LIVE_ENABLED = True
    return settings


def integration(world, kind, *, organisation=None, config=None, secret=None, **fields):
    found = Integration(
        organisation=organisation or world.org_a,
        kind=kind,
        name=fields.pop("name", kind),
        config=config or {},
        **fields,
    )
    if secret:
        secrets.store(found, secret)
    found.save()
    return found


def action(world, kind, key=None, *, integration=None, organisation=None, **fields):
    return ApprovedAction.objects.create(
        organisation=organisation or world.org_a,
        key=key or kind.replace("_", "-"),
        label=fields.pop("label", kind),
        kind=kind,
        integration=integration,
        **fields,
    )


def open_weekdays(campus, opens=time(9), closes=time(13)):
    for weekday in range(7):
        OpeningHours.objects.create(
            campus=campus, weekday=weekday, opens=opens, closes=closes
        )


def tomorrow_at(hour, minute=0):
    day = timezone.localdate() + timedelta(days=1)
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def consented_lead(world, campus=None, *, whatsapp=False):
    lead = world.lead(world.admin_a, campus or world.north, subject="Campus visit")
    contact = lead.contact
    contact.consent = Contact.Consent.GRANTED
    contact.whatsapp_consent = whatsapp
    contact.save()
    return lead


def ctx(world, lead=None, **fields):
    return actions.Context(
        organisation=world.org_a, actor=world.admin_a, lead=lead, **fields
    )


# --- secrets, URL safety and input validation ---------------------------------------


def test_secrets_are_encrypted_and_never_readable_from_the_row(world):
    found = integration(
        world, Integration.Kind.REST_API, secret={"token": "tok-very-secret-1234"}
    )
    row = Integration.objects.filter(pk=found.pk).values().get()
    assert "tok-very-secret-1234" not in json.dumps(row, default=str)
    assert found.secret_hint == "token …1234"
    assert secrets.read(found) == {"token": "tok-very-secret-1234"}
    # An empty value keeps what is stored; a new one replaces it.
    secrets.store(found, {"token": ""})
    assert secrets.read(found)["token"] == "tok-very-secret-1234"


@pytest.mark.parametrize(
    "url, message",
    [
        ("http://api.example.com", "https"),
        ("ftp://api.example.com", "https"),
        ("https://user:pw@api.example.com", "credentials"),
        ("https://api.example.com/?a=1", "query"),
        ("https://", "no host"),
    ],
)
def test_base_urls_must_be_plain_https(url, message):
    with pytest.raises(safety.UnsafeRequestError, match=message):
        safety.check_base_url(url)


def test_inputs_cannot_change_where_a_request_goes():
    base = "https://api.example.com/v1"
    url = safety.build_url(base, "/students/{id}", {"id": "../../admin?x=1#@evil.com"})
    assert (
        url
        == "https://api.example.com/v1/students/..%2F..%2Fadmin%3Fx%3D1%23%40evil.com"
    )
    for path in ("students", "/../x", "//evil.com/x"):
        with pytest.raises(safety.UnsafeRequestError):
            safety.build_url(base, path, {})
    with pytest.raises(safety.UnsafeRequestError, match="Missing input"):
        safety.build_url(base, "/s/{id}", {})


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "localhost", "10.0.0.5", "169.254.169.254"]
)
def test_private_hosts_are_refused(host):
    with pytest.raises(safety.UnsafeRequestError, match="private network"):
        safety.check_destination(f"https://{host}/x")


def test_the_schema_checks_types_formats_and_unknown_inputs():
    rules = schema.check_schema(
        {
            "properties": {
                "id": {"type": "string", "pattern": "[A-Z]{2}[0-9]{4}"},
                "count": {"type": "integer", "minimum": 1, "maximum": 3},
                "when": {"type": "string", "format": "date"},
                "phone": {"type": "string", "format": "phone"},
                "ok": {"type": "boolean"},
            },
            "required": ["id"],
        }
    )
    assert schema.validate(
        rules,
        {"id": "AB1234", "count": "2", "when": "2026-12-01", "phone": "98765 00001"},
    ) == {"id": "AB1234", "count": 2, "when": "2026-12-01", "phone": ASHA, "ok": False}
    for bad, message in (
        ({}, "id is required"),
        ({"id": "x"}, "expected form"),
        ({"id": "AB1234", "count": 9}, "at most 3"),
        ({"id": "AB1234", "when": "tomorrow"}, "valid date"),
        ({"id": "AB1234", "extra": 1}, "Unknown input"),
        ({"id": "AB1234", "count": True}, "number"),
    ):
        with pytest.raises(schema.SchemaError, match=message):
            schema.validate(rules, bad)
    for bad in (
        {"type": "array"},
        {"properties": {"Bad Name": {"type": "string"}}},
        {"properties": {"a": {"type": "file"}}},
        {"properties": {}, "required": ["x"]},
    ):
        with pytest.raises(schema.SchemaError):
            schema.check_schema(bad)


# --- running actions -----------------------------------------------------------------


def test_actions_are_refused_across_organisations_and_when_off(world, no_network):
    theirs = action(world, Kind.CREATE_FOLLOW_UP, organisation=world.org_b)
    lead = consented_lead(world)
    run = actions.run(theirs, {"title": "x"}, ctx(world, lead))
    assert run.status == "refused" and "another organisation" in run.message
    mine = action(world, Kind.CREATE_FOLLOW_UP, is_enabled=False)
    assert actions.run(mine, {"title": "x"}, ctx(world, lead)).status == "refused"
    mine.is_enabled = True
    mine.save()
    not_allowed = actions.run(
        mine, {"title": "x"}, ctx(world, lead, allowed=("custom:other",))
    )
    assert "not allowed this action" in not_allowed.message
    bad = actions.run(mine, {"title": "x", "url": "https://evil"}, ctx(world, lead))
    assert bad.status == "refused" and "Unknown input" in bad.message
    no_lead = actions.run(mine, {"title": "x"}, ctx(world))
    assert "needs a lead" in no_lead.message
    assert not FollowUp.objects.exists()


def test_the_campus_calendar_finds_slots_and_books_them_once(world, no_network):
    world.north.slot_minutes = 60
    world.north.save()
    open_weekdays(world.north)
    calendar = integration(world, Integration.Kind.CAMPUS_CALENDAR)
    check = action(world, Kind.CHECK_AVAILABILITY, integration=calendar)
    book = action(world, Kind.BOOK_APPOINTMENT, integration=calendar)
    lead = consented_lead(world)
    day = (timezone.localdate() + timedelta(days=1)).isoformat()
    run = actions.run(check, {"date": day}, ctx(world, lead))
    assert run.status == "succeeded" and run.output["slots"] == [
        "09:00",
        "10:00",
        "11:00",
        "12:00",
    ]

    booked = actions.run(book, {"start": tomorrow_at(10).isoformat()}, ctx(world, lead))
    assert booked.status == "succeeded", booked.message
    appointment = Appointment.objects.get()
    assert appointment.campus == world.north and appointment.lead == lead
    assert appointment.title == "Visit: Asha Rao"
    again = actions.run(book, {"start": tomorrow_at(10).isoformat()}, ctx(world, lead))
    assert again.status == "failed" and "not a free slot" in again.message
    after = actions.run(check, {"date": day}, ctx(world, lead))
    assert "10:00" not in after.output["slots"]
    odd = actions.run(
        book, {"start": tomorrow_at(10, 30).isoformat()}, ctx(world, lead)
    )
    assert odd.status == "failed"
    # A failure is announced to the organisation.
    assert Notification.objects.filter(kind="action").exists()


def test_holidays_close_the_calendar_and_the_campus(world):
    open_weekdays(world.north)
    tomorrow = timezone.localdate() + timedelta(days=1)
    Holiday.objects.create(organisation=world.org_a, date=tomorrow, name="Festival")
    assert hours.hours_on(world.north, tomorrow) is None
    assert not hours.is_open(world.north, tomorrow_at(10))
    nxt = hours.next_open(world.north, tomorrow_at(10))
    assert nxt.date() == tomorrow + timedelta(days=1) and nxt.hour == 9
    assert hours.describe(world.north) == "Mon–Sun 09:00–13:00"
    # A campus without hours is always open, except on its holidays.
    assert hours.is_open(world.south, tomorrow_at(23)) is False
    assert hours.is_open(world.south, tomorrow_at(23) + timedelta(days=1))


def test_google_calendar_is_simulated_unless_live(world, no_network, monkeypatch):
    open_weekdays(world.north)
    google = integration(
        world,
        Integration.Kind.GOOGLE_CALENDAR,
        config={"client_id": "cid", "calendar_id": "school@example.com"},
        secret={"client_secret": "cs-123456", "refresh_token": "rt-123456"},
    )
    book = action(world, Kind.BOOK_APPOINTMENT, integration=google)
    lead = consented_lead(world)
    run = actions.run(book, {"start": tomorrow_at(10).isoformat()}, ctx(world, lead))
    assert run.status == "simulated"
    request = run.output["request"]
    assert request["url"].endswith("/calendars/school%40example.com/events")
    assert (
        "rt-123456" not in json.dumps(run.output) and not Appointment.objects.exists()
    )


def test_google_calendar_live_refreshes_a_token_and_books(world, live, monkeypatch):
    open_weekdays(world.north)
    google = integration(
        world,
        Integration.Kind.GOOGLE_CALENDAR,
        config={"client_id": "cid", "calendar_id": "primary"},
        secret={"client_secret": "cs-123456", "refresh_token": "rt-123456"},
    )
    fake = FakeTransport(
        monkeypatch, (200, {"access_token": "at-1"}), (200, {"id": "evt-9"})
    )
    lead = consented_lead(world)
    run = actions.run(
        action(world, Kind.BOOK_APPOINTMENT, integration=google),
        {"start": tomorrow_at(11).isoformat()},
        ctx(world, lead),
    )
    assert run.status == "succeeded" and run.output == {"event": "evt-9"}
    token, event = fake.sent
    assert token["url"] == "https://oauth2.googleapis.com/token"
    assert (
        b"grant_type=refresh_token" in token["body"] and b"rt-123456" in token["body"]
    )
    assert event["headers"]["Authorization"] == "Bearer at-1"
    assert json.loads(event["body"])["start"]["timeZone"] == "Asia/Kolkata"
    assert Appointment.objects.get().external_id == "evt-9"
    assert "at-1" not in json.dumps(ActionRun.objects.values().get(), default=str)


def test_whatsapp_needs_permission_and_an_approved_template(world, no_network):
    account = integration(
        world,
        Integration.Kind.WHATSAPP,
        config={"phone_number_id": "1234567890", "templates": ["visit_details"]},
        secret={"access_token": "EAAG-secret"},
    )
    send = action(
        world,
        Kind.WHATSAPP_TEMPLATE,
        integration=account,
        settings={
            "template": "visit_details",
            "language": "en",
            "parameters": ["contact_name"],
        },
    )
    lead = consented_lead(world)
    refused = actions.run(send, {}, ctx(world, lead))
    assert refused.status == "failed" and "not agreed to WhatsApp" in refused.message
    lead.contact.whatsapp_consent = True
    lead.contact.save()
    run = actions.run(send, {}, ctx(world, lead))
    assert run.status == "simulated"
    message = run.output["request"]["json"]
    assert (
        message["to"] == "+91********01"
        and message["template"]["name"] == "visit_details"
    )
    assert message["template"]["components"][0]["parameters"] == [
        {"type": "text", "text": "Asha Rao"}
    ]
    assert_no_full_number(json.dumps(run.output))
    send.settings = {**send.settings, "template": "promo_blast"}
    send.save()
    assert "approved list" in actions.run(send, {}, ctx(world, lead)).message


def test_whatsapp_live_sends_the_template_to_the_contact(world, live, monkeypatch):
    account = integration(
        world,
        Integration.Kind.WHATSAPP,
        config={
            "phone_number_id": "1234567890",
            "templates": ["visit_details"],
            "api_version": "v21.0",
        },
        secret={"access_token": "EAAG-secret"},
    )
    fake = FakeTransport(monkeypatch, (200, {"messages": [{"id": "wamid.1"}]}))
    lead = consented_lead(world, whatsapp=True)
    run = actions.run(
        action(
            world,
            Kind.WHATSAPP_TEMPLATE,
            integration=account,
            settings={"template": "visit_details", "language": "te"},
        ),
        {},
        ctx(world, lead),
    )
    assert run.status == "succeeded" and run.output == {"message": "wamid.1"}
    (sent,) = fake.sent
    assert sent["url"] == "https://graph.facebook.com/v21.0/1234567890/messages"
    assert json.loads(sent["body"])["to"] == ASHA.lstrip("+")
    assert sent["headers"]["Authorization"] == "Bearer EAAG-secret"
    assert_no_full_number(json.dumps(run.output) + run.message)


def test_rest_requests_use_only_the_approved_endpoint(
    world, live, monkeypatch, settings
):
    service = integration(
        world,
        Integration.Kind.REST_API,
        config={
            "base_url": "https://erp.example.com/api",
            "auth_header": "Authorization",
            "auth_scheme": "Bearer",
        },
        secret={"token": "erp-token-777"},
    )
    lookup = action(
        world,
        Kind.REST_REQUEST,
        integration=service,
        settings={
            "method": "POST",
            "path": "/admissions/{class_name}/seats",
            "body_fields": ["class_name"],
            "response_fields": ["seats"],
        },
        input_schema={
            "properties": {"class_name": {"type": "string", "maxLength": 20}},
            "required": ["class_name"],
        },
    )
    monkeypatch.setattr(safety, "check_destination", lambda url: None)
    fake = FakeTransport(monkeypatch, (200, {"seats": 4, "internal": "hidden"}))
    run = actions.run(lookup, {"class_name": "Class 6"}, ctx(world))
    assert run.status == "succeeded" and run.output == {
        "status": 200,
        "data": {"seats": 4},
    }
    (sent,) = fake.sent
    assert sent["url"] == "https://erp.example.com/api/admissions/Class%206/seats"
    assert sent["headers"]["Authorization"] == "Bearer erp-token-777"
    assert json.loads(sent["body"]) == {"class_name": "Class 6"}
    FakeTransport(monkeypatch, (500, {}))
    failed = actions.run(lookup, {"class_name": "Class 6"}, ctx(world))
    assert failed.status == "failed" and "HTTP 500" in failed.message


def test_lead_updates_follow_ups_and_hand_over_to_a_person(world, no_network):
    lead = consented_lead(world)
    world.north.escalation_user = world.staff_north
    world.north.save()
    run = actions.run(
        action(world, Kind.UPDATE_LEAD),
        {"stage": "interested", "note": "Keen"},
        ctx(world, lead),
    )
    lead.refresh_from_db()
    assert run.status == "succeeded" and lead.stage == "interested"
    assert lead.notes.get().text == "Keen"
    actions.run(
        action(world, Kind.CREATE_FOLLOW_UP),
        {"title": "Send brochure"},
        ctx(world, lead),
    )
    escalation = actions.run(action(world, Kind.ESCALATE), {}, ctx(world, lead))
    assert escalation.status == "succeeded" and "not available" in escalation.message
    handover = FollowUp.objects.get(title__startswith="Call back")
    assert handover.assigned_to == world.staff_north
    note = Notification.objects.get(kind="escalation")
    assert note.user == world.staff_north and "Asha Rao" in note.title


# --- webhooks ----------------------------------------------------------------------------


def webhook(world, events=("call.analysed",), url="https://hooks.example.com/in"):
    return integration(
        world,
        Integration.Kind.WEBHOOK,
        config={"url": url, "events": list(events)},
        secret={"signing_secret": "whsec-abcdef"},
    )


def test_events_go_only_to_subscribed_webhooks_of_the_organisation(world, no_network):
    hook = webhook(world, events=("lead.created",))
    webhook(world, events=("call.analysed",))
    integration(
        world,
        Integration.Kind.WEBHOOK,
        organisation=world.org_b,
        config={"url": "https://other.example.com", "events": ["lead.created"]},
    )
    created = webhooks.emit(world.org_a, "lead.created", {"lead": "1"})
    assert [d.integration for d in created] == [hook]
    assert webhooks.emit(world.org_a, "not.an.event", {}) == []
    # Not live: recorded as simulated, never sent.
    assert webhooks.deliver(created[0].pk).status == "simulated"


def test_live_delivery_is_signed_and_retried_then_given_up(world, live, monkeypatch):
    monkeypatch.setattr(safety, "check_destination", lambda url: None)
    hook = webhook(world)
    (delivery,) = webhooks.emit(world.org_a, "call.analysed", {"call": "x"})
    fake = FakeTransport(monkeypatch, (204, {}))
    result = webhooks.deliver(delivery.pk)
    assert result.status == "delivered" and result.attempts == 1
    sent = fake.sent[0]
    expected = hmac.new(
        b"whsec-abcdef",
        sent["headers"]["X-Webhook-Timestamp"].encode() + b"." + sent["body"],
        hashlib.sha256,
    ).hexdigest()
    assert sent["headers"]["X-Webhook-Signature"] == expected
    assert webhooks.deliver(delivery.pk) is None, "a delivered event is not sent twice"

    (failing,) = webhooks.emit(world.org_a, "call.analysed", {"call": "y"})
    FakeTransport(monkeypatch, *[(500, {})] * 10)
    gaps = []
    for _ in range(webhooks.MAX_ATTEMPTS):
        WebhookDelivery.objects.filter(pk=failing.pk).update(next_attempt_at=None)
        result = webhooks.deliver(failing.pk)
        if result.next_attempt_at:
            gaps.append(
                round((result.next_attempt_at - timezone.now()).total_seconds() / 60)
            )
    assert gaps == [1, 2, 4, 8, 16]
    assert result.status == "failed" and result.attempts == webhooks.MAX_ATTEMPTS
    assert (
        Notification.objects.filter(kind="webhook", organisation=world.org_a).count()
        == 1
    )
    assert hook.deliveries.count() == 2


# --- analysis: retries, clean-up, and actions after the call ---------------------------


def analysed_call(world, *, actions_allowed=(), rehearsal=False, lead=None):
    employee = Employee.objects.create(
        organisation=world.org_a,
        name="Meera",
        role="r",
        brief="b" * 30,
        language="en",
        draft=employee_config.normalize(
            {
                "agent_name": "Meera",
                "greeting": "Hello, I am an AI assistant.",
                "purpose": "Follow up.",
                "steps": [{"name": "Ask", "guidance": "Ask."}],
                "actions": list(actions_allowed),
            }
        ),
    )
    publishing.publish(employee, world.admin_a)
    employee.refresh_from_db()
    plan = publishing.plan_call(
        employee, campus=world.north, lead=lead, rehearsal=rehearsal
    )
    record = CallRecord.objects.create(
        organisation=world.org_a,
        campus=world.north,
        lead=lead,
        room=f"room-{CallRecord.objects.count()}",
        masked_number="+91********10",
        status="completed",
        employee_version=plan.version,
        context_snapshot=plan.snapshot,
    )
    CallTranscript.objects.create(
        call=record, turns=[{"role": "user", "text": "Please book a visit."}]
    )
    return record


def canned(**fields):
    answer = {
        "summary": "Wants a visit.",
        "outcome": "interested",
        "answers": {},
        "callback_requested": False,
        "callback_when": "",
        "opt_out_requested": False,
        "appointment_at": "",
        "whatsapp_requested": False,
        "human_requested": False,
        **fields,
    }
    return lambda system, user, **kwargs: answer


def test_failed_analysis_is_retried_with_growing_gaps_then_cleaned_up(world):
    from apps.workforce import llm

    record = analysed_call(world)

    def down(*args, **kwargs):
        raise llm.LLMError("The LLM service answered HTTP 503.")

    gaps = []
    for _ in range(analysis.MAX_ATTEMPTS):
        result = analysis.analyse(record, complete=down)
        if result.next_attempt_at:
            gaps.append(
                round((result.next_attempt_at - timezone.now()).total_seconds() / 60)
            )
    assert gaps == [2, 4, 8, 16]
    assert result.status == "failed" and "Gave up after 5 attempts" in result.error
    # The organisation analyses then deletes: the transcript goes all the same.
    assert not CallTranscript.objects.filter(call=record).exists()
    assert not analysis.pending().exists()


def test_a_retry_becomes_due_and_succeeds(world):
    from apps.workforce import llm

    record = analysed_call(world)

    def down(*args, **kwargs):
        raise llm.LLMError("timeout")

    analysis.analyse(record, complete=down)
    assert not analysis.pending().exists(), "not due yet"
    assert analysis.pending(now=timezone.now() + timedelta(minutes=3)).count() == 1
    result = analysis.analyse(record, complete=canned())
    assert result.status == "done" and result.attempts == 2
    assert not CallTranscript.objects.filter(call=record).exists()


def test_a_reported_transcript_is_analysed_after_commit(
    world, django_capture_on_commit_callbacks, monkeypatch
):
    from apps.workforce import llm

    monkeypatch.setattr(llm, "complete_json", canned(outcome="callback"))
    record = analysed_call(world)
    CallTranscript.objects.filter(call=record).delete()
    with django_capture_on_commit_callbacks(execute=True):
        analysis.store_transcript(record, [{"role": "user", "text": "Call me Friday."}])
    result = CallResult.objects.get(call=record)
    assert result.status == "done" and result.outcome == "callback"
    assert not CallTranscript.objects.filter(call=record).exists()


def test_actions_follow_the_call_only_when_allowed_and_never_on_rehearsals(
    world, no_network, django_capture_on_commit_callbacks
):
    open_weekdays(world.north)
    calendar = integration(world, Integration.Kind.CAMPUS_CALENDAR)
    action(world, Kind.BOOK_APPOINTMENT, "book-visit", integration=calendar)
    action(world, Kind.ESCALATE, "hand-over")
    hook = webhook(world)
    lead = consented_lead(world)
    when = tomorrow_at(10).strftime("%Y-%m-%dT%H:%M")

    real = analysed_call(world, actions_allowed=["custom:book-visit"], lead=lead)
    with django_capture_on_commit_callbacks(execute=True):
        analysis.analyse(
            real, complete=canned(appointment_at=when, human_requested=True)
        )
    (run,) = ActionRun.objects.filter(call=real)
    assert run.status == "succeeded" and run.trigger == "call"
    assert Appointment.objects.get().lead == lead
    assert not FollowUp.objects.exists(), "hand-over was not allowed for this employee"
    assert real.context_snapshot["action_details"][0]["label"] == "book_appointment"
    delivery = hook.deliveries.get()
    assert delivery.event == "call.analysed" and delivery.status == "simulated"
    assert delivery.payload["data"]["outcome"] == "interested"
    assert_no_full_number(json.dumps(delivery.payload))

    rehearsal = analysed_call(
        world, actions_allowed=["custom:book-visit"], rehearsal=True, lead=lead
    )
    analysis.analyse(
        rehearsal,
        complete=canned(appointment_at=tomorrow_at(11).strftime("%Y-%m-%dT%H:%M")),
    )
    refused = ActionRun.objects.get(call=rehearsal)
    assert refused.status == "refused" and "Rehearsal" in refused.message
    assert Appointment.objects.count() == 1
    # A time in the past, or not a time at all, books nothing.
    for bad in ("2020-01-01T10:00", "next Friday"):
        late = analysed_call(world, actions_allowed=["custom:book-visit"], lead=lead)
        analysis.analyse(late, complete=canned(appointment_at=bad))
        assert not ActionRun.objects.filter(call=late).exists()


def test_an_employee_may_name_only_its_organisations_actions(world):
    assert employee_config.normalize(
        {"actions": ["custom:book-visit", "custom:Bad Key", 7]}
    )["actions"] == ["custom:book-visit"]
    action(world, Kind.ESCALATE, "hand-over")
    employee = Employee.objects.create(
        organisation=world.org_a, name="E", role="r", brief="b" * 30
    )
    assert publishing.unknown_actions(
        employee, ["custom:hand-over", "custom:nope"]
    ) == ["nope"]


# --- background jobs -----------------------------------------------------------------------


def test_a_job_never_runs_twice_at_once_and_records_failures(world):
    cache.add("job-lock:usage_report", 1, timeout=60)
    assert jobs.usage_report() == {}
    assert JobRun.objects.get(name="usage_report").status == "skipped"
    cache.delete("job-lock:usage_report")
    with pytest.raises(ZeroDivisionError), jobs.tracked("broken"):
        raise ZeroDivisionError("a job failed")
    broken = JobRun.objects.get(name="broken")
    assert broken.status == "failed" and "ZeroDivisionError" in broken.error
    assert cache.get("job-lock:broken") is None


def test_follow_up_reminders_are_sent_once(world):
    lead = consented_lead(world)
    FollowUp.objects.create(
        lead=lead,
        organisation=world.org_a,
        campus=world.north,
        title="Ring",
        due_at=timezone.now() - timedelta(minutes=5),
        assigned_to=world.staff_north,
    )
    FollowUp.objects.create(
        lead=lead,
        organisation=world.org_a,
        campus=world.north,
        title="Later",
        due_at=timezone.now() + timedelta(days=1),
    )
    assert jobs.follow_up_reminders()["notified"] == 1
    assert jobs.follow_up_reminders()["notified"] == 0
    note = Notification.objects.get()
    assert note.user == world.staff_north and "Ring" in note.title


def test_campaign_checks_skip_people_who_opted_out_since(world):
    lead = consented_lead(world)
    entry = QueuedCall.objects.create(
        organisation=world.org_a,
        campus=world.north,
        contact=lead.contact,
        status="would_call",
    )
    assert jobs.campaign_eligibility()["skipped_now"] == 0
    contacts.opt_out_number(world.org_a, ASHA)
    assert jobs.campaign_eligibility()["skipped_now"] == 1
    entry.refresh_from_db()
    assert entry.status == "skipped" and "do-not-call" in entry.reason


def test_usage_is_reported_per_day_campus_and_employee(world):
    record = analysed_call(world)
    CallRecord.objects.filter(pk=record.pk).update(
        answered_at=timezone.now(), duration_seconds=95
    )
    analysis.analyse(record, complete=canned())
    jobs.usage_report()
    jobs.usage_report()  # running twice changes nothing
    row = UsageDay.objects.get()
    assert (row.calls, row.answered, row.talk_seconds, row.analyses) == (1, 1, 95, 1)
    assert row.campus == world.north and row.employee.name == "Meera"


def test_the_beat_schedule_names_real_tasks():
    from django.conf import settings

    from config.celery import app

    app.loader.import_default_modules()
    for entry in settings.CELERY_BEAT_SCHEDULE.values():
        assert entry["task"] in app.tasks, entry["task"]


def test_new_leads_are_announced_to_webhooks(world, no_network):
    hook = webhook(world, events=("lead.created",))
    lead = consented_lead(world)
    webhooks.emit(world.org_a, "lead.created", {"lead": str(lead.pk)})
    assert hook.deliveries.count() == 1
    assert Lead.objects.count() == 1
