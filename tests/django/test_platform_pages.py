"""Milestone E dashboard pages: WhatsApp permission, campus hours and
holidays, missed callers, voice previews, employee actions and the operations,
usage and notification pages. No test reaches the network: the outside-request
transport is replaced."""

import base64
import json
from datetime import time

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.calling.models import CallResult, MissedCall
from apps.crm.models import Contact
from apps.integrations import transport
from apps.operations.models import Notification
from apps.telephony.models import CallRecord
from apps.tenancy import hours
from apps.tenancy.models import AuditEvent, Holiday, OpeningHours
from apps.workforce import publishing

from .test_calling import employee_for
from .test_tenancy_crm import World, body

pytestmark = pytest.mark.django_db


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


def lead_page(client, lead) -> str:
    return body(client.get(reverse("lead-detail", args=[lead.pk])))


# --- WhatsApp permission ----------------------------------------------------------------


def test_whatsapp_permission_needs_evidence_and_is_audited(world):
    contact = world.contact(world.admin_a, world.north)
    staff = world.client(world.staff_north)
    url = reverse("contact-detail", args=[contact.pk])

    response = staff.post(
        url, {"action": "whatsapp", "whatsapp_consent": "on"}, follow=True
    )
    assert "Say how and when they agreed" in body(response)
    contact.refresh_from_db()
    assert contact.whatsapp_consent is False

    staff.post(
        url,
        {
            "action": "whatsapp",
            "whatsapp_consent": "on",
            "whatsapp_evidence": "Ticked the WhatsApp box on the enquiry form, 3 Nov.",
        },
    )
    contact.refresh_from_db()
    assert contact.whatsapp_consent and contact.whatsapp_consent_at
    assert AuditEvent.objects.get(action="contact.whatsapp_consent").detail == {
        "agreed": True
    }

    staff.post(url, {"action": "whatsapp", "whatsapp_evidence": "ignored"})
    contact.refresh_from_db()
    assert not contact.whatsapp_consent and contact.whatsapp_consent_evidence == ""
    other = world.client(world.admin_b)
    assert other.post(url, {"action": "whatsapp"}).status_code == 404


# --- campus hours, holidays and call handling -------------------------------------------


def _hours_post(days, opens="09:00", closes="16:00"):
    data = {"action": "hours"}
    for day in days:
        data[f"hours-open_{day}"] = "on"
        data[f"hours-opens_{day}"] = opens
        data[f"hours-closes_{day}"] = closes
    return data


def test_admins_set_campus_hours_holidays_and_call_handling(world):
    admin = world.client(world.admin_a)
    url = reverse("campus-edit", args=[world.north.pk])
    assert "always open" in body(admin.get(url))

    response = admin.post(url, _hours_post(range(5)), follow=True)
    assert "Opening hours saved: Mon–Fri 09:00–16:00." in body(response)
    assert hours.describe(world.north) == "Mon–Fri 09:00–16:00"

    bad = admin.post(url, _hours_post([5], opens="13:00", closes="09:00"))
    assert "must close after it opens" in body(bad)
    assert not OpeningHours.objects.filter(campus=world.north, weekday=5).exists()
    missing = admin.post(url, {"action": "hours", "hours-open_6": "on"})
    assert "opening and closing times" in body(missing)

    day = timezone.localdate() + timezone.timedelta(days=10)
    admin.post(
        url,
        {
            "action": "holiday",
            "holiday-date": day.isoformat(),
            "holiday-name": "Founders' Day",
            "holiday-whole_organisation": "on",
        },
    )
    holiday = Holiday.objects.get()
    assert holiday.campus is None and holiday.organisation == world.org_a
    again = admin.post(
        url,
        {"action": "holiday", "holiday-date": day.isoformat(), "holiday-name": "Again"},
    )
    assert "already a holiday" in body(again)
    south_page = body(admin.get(reverse("campus-edit", args=[world.south.pk])))
    assert "Founders" in south_page and "Every location" in south_page

    other_org = Holiday.objects.create(
        organisation=world.org_b, date=day, name="Other organisation's day"
    )
    admin.post(url, {"action": "holiday_remove", "holiday": str(other_org.pk)})
    assert Holiday.objects.filter(pk=other_org.pk).exists()
    admin.post(url, {"action": "holiday_remove", "holiday": str(holiday.pk)})
    assert not Holiday.objects.filter(pk=holiday.pk).exists()

    south_staff = World._member(
        "staff-south", world.org_a, "campus_staff", [world.south]
    )
    refused = admin.post(
        url,
        {
            "action": "calls",
            "calls-slot_minutes": "45",
            "calls-after_hours_message": "We reply within a day.",
            "calls-escalation_user": str(south_staff.pk),
        },
    )
    assert "Select a valid choice" in body(refused)
    admin.post(
        url,
        {
            "action": "calls",
            "calls-slot_minutes": "45",
            "calls-after_hours_message": "  We reply   within a day. ",
            "calls-escalation_user": str(world.staff_north.pk),
        },
    )
    world.north.refresh_from_db()
    assert world.north.slot_minutes == 45
    assert world.north.after_hours_message == "We reply within a day."
    assert world.north.escalation_user == world.staff_north
    assert AuditEvent.objects.filter(action="campus.hours_changed").exists()

    cleared = admin.post(url, {"action": "hours"}, follow=True)
    assert "always open" in body(cleared)
    assert not OpeningHours.objects.filter(campus=world.north).exists()

    assert world.client(world.staff_north).get(url).status_code == 403
    assert world.client(world.admin_b).get(url).status_code == 404


# --- missed callers -------------------------------------------------------------------


def _missed(world, campus, reason=MissedCall.Reason.AFTER_HOURS):
    call = world.call(world.org_a, campus, direction=CallRecord.Direction.INBOUND)
    return MissedCall.objects.create(
        call=call, reason=reason, detail="A message was taken."
    )


def test_missed_callers_are_listed_by_campus_and_marked_handled(world):
    north = _missed(world, world.north)
    south = _missed(world, world.south, MissedCall.Reason.NOT_ROUTED)
    CallResult.objects.create(call=north.call, summary="Wants the fee sheet.")
    staff = world.client(world.staff_north)

    page = body(staff.get(reverse("inbound")))
    assert 'id="missed-table"' in page and "Wants the fee sheet." in page
    assert "Called after hours" in page and "No employee was ready" not in page

    assert staff.post(reverse("inbound"), {"missed": str(south.pk)}).status_code == 404
    response = staff.post(reverse("inbound"), {"missed": str(north.pk)}, follow=True)
    assert "Marked as called back." in body(response)
    north.refresh_from_db()
    assert north.handled_by == world.staff_north and north.handled_at
    assert 'id="missed-none"' in body(response)
    assert AuditEvent.objects.filter(action="missed.handled").count() == 1

    call_page = body(staff.get(reverse("conversation-detail", args=[north.call.pk])))
    assert 'id="call-missed"' in call_page and "Called back" in call_page

    admin_page = body(world.client(world.admin_a).get(reverse("inbound")))
    assert "No employee was ready" in admin_page
    assert (
        world.client(world.admin_b)
        .post(reverse("inbound"), {"missed": str(south.pk)})
        .status_code
        == 404
    )


# --- voice preview ----------------------------------------------------------------------


def test_voice_preview_speaks_the_line_without_a_call(world, monkeypatch, settings):
    monkeypatch.setenv("SARVAM_TTS_SPEAKER", "kavitha")
    settings.SARVAM_API_KEY = "sk-test-key"
    employee = employee_for(world, publish=False)
    audio = b"ID3-fake-mp3"
    fake = FakeTransport(
        monkeypatch, (200, {"audios": [base64.b64encode(audio).decode()]})
    )
    admin = world.client(world.admin_a)
    url = reverse("employee-voice-preview", args=[employee.pk])
    calls_before = CallRecord.objects.count()

    response = admin.post(
        url, {"text": "నమస్తే, I am Meera.", "speaker": "kavya", "pace": "0.9"}
    )
    assert response.status_code == 200 and response["Content-Type"] == "audio/mpeg"
    assert response.content == audio
    sent = fake.sent[0]
    assert sent["url"] == "https://api.sarvam.ai/text-to-speech"
    assert sent["headers"]["api-subscription-key"] == "sk-test-key"
    request = json.loads(sent["body"])
    assert request["speaker"] == "kavya" and request["pace"] == 0.9
    assert (
        request["model"] == "bulbul:v3" and request["target_language_code"] == "en-IN"
    )
    assert CallRecord.objects.count() == calls_before

    cache.delete(f"voice-preview:{world.admin_a.pk}")
    again = admin.post(
        url, {"text": "నమస్తే, I am Meera.", "speaker": "kavya", "pace": "0.9"}
    )
    assert again.content == audio and len(fake.sent) == 1, "served from the cache"
    assert AuditEvent.objects.filter(action="voice.preview").count() == 1

    throttled = admin.post(url, {"text": "Another line", "speaker": "kavya"})
    assert throttled.status_code == 429

    cache.delete(f"voice-preview:{world.admin_a.pk}")
    admin.post(url, {"text": "Another line", "speaker": "not-a-voice"})
    assert json.loads(fake.sent[1]["body"])["speaker"] == "kavitha"

    usage = body(admin.get(reverse("usage")))
    assert "Voice previews" in usage

    assert world.client(world.staff_north).post(url, {}).status_code == 403
    assert world.client(world.admin_b).post(url, {}).status_code == 404


def test_v4_preview_matches_approved_playground_settings():
    from apps.workforce.preview import options

    request = options("Hello", "shubh_enhi_ads", "1.0", "te-en")
    assert request["model"] == "bulbul:v4-flash"
    assert request["speaker"] == "shubh_enhi_ads"
    assert request["pace"] == 1.0
    assert request["speech_sample_rate"] == 24000
    assert request["target_language_code"] == "te-IN"


def test_voice_preview_explains_why_it_cannot_play(world, monkeypatch, settings):
    employee = employee_for(world, publish=False)
    url = reverse("employee-voice-preview", args=[employee.pk])
    admin = world.client(world.admin_a)

    settings.SARVAM_API_KEY = ""
    response = admin.post(url, {"text": "Hello"})
    assert response.status_code == 502 and "SARVAM_API_KEY" in response.json()["error"]
    assert "Voice previews need SARVAM_API_KEY" in body(
        admin.get(reverse("employee-detail", args=[employee.pk]))
    )

    settings.SARVAM_API_KEY = "sk-test-key"
    cache.clear()
    FakeTransport(monkeypatch, (403, {"error": "bad key"}))
    response = admin.post(url, {"text": "Hello"})
    assert response.status_code == 502 and "403" in response.json()["error"]
    assert not AuditEvent.objects.filter(action="voice.preview").exists()


# --- employee actions and where it works ------------------------------------------------


def test_employees_keep_only_built_in_actions(world):
    employee = employee_for(world, publish=False)
    admin = world.client(world.admin_a)
    url = reverse("employee-detail", args=[employee.pk])

    page = body(admin.get(url))
    assert 'id="where-it-works"' in page and 'id="activity"' in page
    assert 'id="voice-preview"' in page
    assert "approved-actions" not in page

    data = {
        "name": "Meera",
        "role": "Enquiry assistant",
        "channel": "instant",
        "language": "en",
        "greeting": employee.draft["greeting"],
        "purpose": employee.draft["purpose"],
        "limits": employee.draft["limits"],
        "ending": employee.draft["ending"],
        "strictness": "3",
        "step_name": ["Ask"],
        "step_guidance": ["Ask what they need."],
        # An old organisation action key is dropped, not kept.
        "action": ["create_follow_up", "custom:visit-task"],
    }
    admin.post(url, data)
    employee.refresh_from_db()
    assert employee.draft["actions"] == ["create_follow_up"]
    version = publishing.publish(employee, world.admin_a)
    assert version.config["actions"] == ["create_follow_up"]


# --- operations, usage, notifications and readiness -------------------------------------


def test_operations_and_usage_pages_are_for_admins(world):
    admin, staff = world.client(world.admin_a), world.client(world.staff_north)
    assert staff.get(reverse("operations")).status_code == 403
    assert staff.get(reverse("usage")).status_code == 403
    page = body(admin.get(reverse("operations")))
    assert 'id="job-table"' in page and "REDIS_URL" in page

    world.org_a.cost_per_minute = 2
    world.org_a.save()
    world.call(
        world.org_a,
        world.north,
        answered_at=timezone.now(),
        duration_seconds=90,
    )
    world.call(world.org_b, world.west, duration_seconds=600)
    usage = body(admin.get(reverse("usage")))
    assert '<p class="tile-value" id="usage-calls">1</p>' in usage
    assert "₹3.00" in usage


def test_notifications_follow_campus_access_and_can_be_marked_read(world):
    Notification.objects.create(
        organisation=world.org_a,
        campus=world.north,
        kind="missed_call",
        title="North caller",
    )
    Notification.objects.create(
        organisation=world.org_a,
        campus=world.south,
        kind="missed_call",
        title="South caller",
    )
    Notification.objects.create(
        organisation=world.org_a,
        user=world.admin_a,
        kind="job",
        title="For the admin only",
    )
    Notification.objects.create(
        organisation=world.org_b, kind="job", title="Other organisation"
    )
    staff = world.client(world.staff_north)
    page = body(staff.get(reverse("notifications")))
    assert "North caller" in page
    for hidden in ("South caller", "For the admin only", "Other organisation"):
        assert hidden not in page
    assert 'class="badge"' in body(staff.get(reverse("home")))

    staff.post(reverse("notifications"))
    north = Notification.objects.get(title="North caller")
    assert north.read_at is not None
    assert Notification.objects.get(title="South caller").read_at is None


def test_readiness_reports_booleans_only(world, monkeypatch, client):
    from apps.operations import views as operations_views

    response = client.get(reverse("readiness"))
    assert response.status_code == 503
    checks = response.json()
    assert checks["database"] is True and checks["job_broker"] is False
    assert all(isinstance(value, bool) for value in checks.values())

    monkeypatch.setattr(operations_views, "_broker", lambda: "")
    monkeypatch.setattr("apps.crm.crypto.configured", lambda: True)
    assert client.get(reverse("readiness")).json()["ready"] is True


def test_after_hours_line_uses_the_campus_message(world):
    from apps.calling.inbound import after_hours_notice

    for weekday in range(7):
        OpeningHours.objects.create(
            campus=world.north, weekday=weekday, opens=time(9), closes=time(9, 1)
        )
    world.north.after_hours_message = "Fees are on the website."
    world.north.save()
    notice = after_hours_notice(world.north)
    assert notice.startswith("North is closed now.")
    assert notice.endswith("Fees are on the website.")
    assert Contact.objects.count() == 0


# --- configuration --------------------------------------------------------------------


def test_every_django_setting_is_documented_in_env_example():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "config" / "settings.py").read_text(encoding="utf-8")
    documented = (root / ".env.example").read_text(encoding="utf-8")
    names = set(
        re.findall(
            r'(?:os\.environ\.get|_env_bool|_env_list)\(\s*"([A-Z][A-Z0-9_]+)"', source
        )
    )
    assert len(names) > 15
    assert [n for n in sorted(names) if f"\n{n}=" not in documented] == []


def test_production_settings_pass_the_deploy_check():
    """`manage.py check --deploy` with production-like settings: HTTPS only,
    secure cookies, HSTS, a long secret and no debug."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "DJANGO_DEBUG": "false",
        "DJANGO_SECRET_KEY": "p" * 20
        + "rod-like-secret-key-with-enough-variety-0123456789",
        "DJANGO_ALLOWED_HOSTS": "calls.example.org",
        "DJANGO_CSRF_TRUSTED_ORIGINS": "https://calls.example.org",
        "DJANGO_HSTS_SECONDS": "31536000",
        "DJANGO_HSTS_INCLUDE_SUBDOMAINS": "true",
        "DJANGO_SSL_REDIRECT": "",
        "DJANGO_SECURE_COOKIES": "",
        "DATABASE_URL": "sqlite://:memory:",
        "REDIS_URL": "",
        "PYTHONUTF8": "1",
    }
    env.pop("TEST_DATABASE_URL", None)
    result = subprocess.run(
        [sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
