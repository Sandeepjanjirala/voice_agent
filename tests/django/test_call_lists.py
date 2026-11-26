"""Make calls: numbers typed or uploaded (Excel, CSV), checked, confirmed, and
called one after another (apps/calling/calllists.py). LiveKit is the fake
gateway and the numbers are made up; no call leaves the test."""

# The fixtures imported from other test modules are named as test parameters.
# ruff: noqa: F811

import io
import json
import os
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.calling import calllists
from apps.calling.models import CallList, CallListEntry
from apps.crm import contacts
from apps.telephony.models import CallRecord
from apps.tenancy.models import AuditEvent

from .test_calling import employee_for
from .test_calls import env, gateway, post_event  # noqa: F401
from .test_tenancy_crm import World, assert_no_full_number, body

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def at_eleven(monkeypatch):
    fixed = timezone.make_aware(datetime.combine(timezone.localdate(), time(11, 0)))
    monkeypatch.setattr(timezone, "now", lambda: fixed)
    return fixed


@pytest.fixture
def world(env, gateway, at_eleven):
    env.setenv("OUTBOUND_DIALING_MODE", "live")
    cache.clear()
    world = World()
    world.employee = employee_for(world)
    return world


def xlsx(rows) -> SimpleUploadedFile:
    from openpyxl import Workbook

    book = Workbook()
    for row in rows:
        book.active.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return SimpleUploadedFile("parents.xlsx", buffer.getvalue())


# --- reading the list -----------------------------------------------------------------


def test_typed_lines_with_or_without_names():
    parsed = calllists.parse(
        "Asha Rao, 98765 43210\n9876543211\n+91 98765-43212 Ravi\n12345\n9876543211\n\n"
    )
    assert parsed.rows == [
        ("Asha Rao", "+919876543210"),
        ("", "+919876543211"),
        ("Ravi", "+919876543212"),
    ]
    assert parsed.duplicates == 1
    assert parsed.rejected == [
        {"row": 4, "text": "***45", "reason": "No phone number found"}
    ]


def test_excel_and_csv_with_headers_or_without():
    with_header = calllists.parse(
        upload=xlsx([("Name", "Mobile"), ("Asha", 9876543210), ("Lata", "98765 43213")])
    )
    assert with_header.rows == [("Asha", "+919876543210"), ("Lata", "+919876543213")]
    # Excel keeps numbers as numbers: 9876543210.0 is still a mobile.
    without_header = calllists.parse(
        upload=xlsx([(9876543214.0, "Ravi"), ("Kiran", "9876543215")])
    )
    assert without_header.rows == [
        ("Ravi", "+919876543214"),
        ("Kiran", "+919876543215"),
    ]
    csv = SimpleUploadedFile(
        "list.csv", b"phone,name\n9876543216,Meena\n123456789012,Bad\n"
    )
    parsed = calllists.parse(upload=csv)
    assert parsed.rows == [("Meena", "+919876543216")]
    assert parsed.rejected[0]["row"] == 3 and "valid" in parsed.rejected[0]["reason"]
    with pytest.raises(calllists.ListError, match="Excel"):
        calllists.parse(upload=SimpleUploadedFile("list.pdf", b"%PDF"))


def test_a_file_too_big_for_one_list_is_refused_not_cut_short():
    rows = [("Name", "Mobile")] + [(f"P{i}", f"98765{i:05d}") for i in range(600)]
    with pytest.raises(calllists.ListError, match="split the file"):
        calllists.parse(upload=xlsx(rows))
    big_csv = "phone\n" + "\n".join(f"98765{i:05d}" for i in range(600))
    with pytest.raises(calllists.ListError, match="split the file"):
        calllists.parse(upload=SimpleUploadedFile("big.csv", big_csv.encode()))
    # Blank rows Excel keeps at the end do not count.
    padded = [("Mobile",), ("9876543210",)] + [(None,)] * 900
    assert calllists.parse(upload=xlsx(padded)).rows == [("", "+919876543210")]


def test_a_list_is_capped():
    text = "\n".join(f"98765{index:05d}" for index in range(calllists.MAX_ROWS + 3))
    parsed = calllists.parse(text)
    assert len(parsed.rows) == calllists.MAX_ROWS and len(parsed.rejected) == 3


# --- the pages --------------------------------------------------------------------------


def make_list(client, world, **fields):
    data = {
        "speaker": str(world.employee.pk),
        "purpose": "Fee reminder",
        "numbers": "Asha, 9876543210\nRavi, 9876543211\nnot a number",
    }
    data.update(fields)
    response = client.post(reverse("make-calls"), data)
    assert response.status_code == 302, body(client.get(reverse("make-calls")))
    return CallList.objects.get(pk=response["Location"].rstrip("/").split("/")[-1])


def test_numbers_are_checked_then_confirmed_before_anyone_is_called(world, gateway):
    admin = world.client(world.admin_a)
    page = body(admin.get(reverse("make-calls")))
    assert "Who speaks" in page and "upload" in page.lower()
    contacts.opt_out_number(
        world.org_a, "+919876543211", reason="Asked", source="caller"
    )
    call_list = make_list(admin, world)
    assert call_list.status == "ready" and call_list.employee == world.employee
    statuses = list(call_list.entries.values_list("status", "reason"))
    assert statuses == [("waiting", ""), ("skipped", "On the do-not-call list")]
    assert call_list.rejected_rows[0]["reason"] == "No phone number found"
    html = body(admin.get(reverse("call-list", args=[call_list.pk])))
    assert (
        "will be called, one after another" in html
        and "Everyone on this list agreed" in html
    )
    assert_no_full_number(html)
    assert "98765" not in html
    # Without the tick nothing starts.
    admin.post(reverse("call-list", args=[call_list.pk]), {"action": "start"})
    call_list.refresh_from_db()
    assert call_list.status == "ready" and gateway.dispatches == []
    admin.post(
        reverse("call-list", args=[call_list.pk]), {"action": "start", "consent": "yes"}
    )
    call_list.refresh_from_db()
    assert (
        call_list.status == "running"
        and "agreed to be called" in call_list.consent_text
    )
    assert AuditEvent.objects.filter(action="call_list.started").exists()


def test_calls_go_one_after_another_with_the_employee_and_end_done(world, gateway):
    admin = world.client(world.admin_a)
    call_list = make_list(admin, world, numbers="Asha, 9876543210\nRavi, 9876543211")
    admin.post(
        reverse("call-list", args=[call_list.pk]), {"action": "start", "consent": "yes"}
    )
    step = reverse("call-list-step", args=[call_list.pk])

    first = admin.post(step).json()
    assert first["tally"]["calling"] == 1 and len(gateway.dispatches) == 1
    metadata = json.loads(gateway.dispatches[0].metadata)
    assert metadata["outbound_call"]["to"] == "+919876543210"
    assert metadata["agent_profile"]["context"] == {
        "name": "Asha",
        "about": "Fee reminder",
    }
    # While the first call is on the line, nobody else is called.
    admin.post(step)
    assert len(gateway.dispatches) == 1

    record = CallRecord.objects.get()
    post_event(Client(), {"room": record.room, "event": "answered"})
    post_event(
        Client(),
        {
            "room": record.room,
            "event": "ended",
            "outcome": "completed",
            "talk_seconds": 42,
        },
    )
    second = admin.post(step).json()
    assert (
        second["entries"][0]["status"] == "answered"
        and second["entries"][0]["call_url"]
    )
    assert len(gateway.dispatches) == 2
    assert (
        json.loads(gateway.dispatches[1].metadata)["outbound_call"]["to"]
        == "+919876543211"
    )

    record = CallRecord.objects.order_by("-created_at").first()
    post_event(
        Client(), {"room": record.room, "event": "ended", "outcome": "no_answer"}
    )
    last = admin.post(step).json()
    assert last["status"] == "done"
    assert [e["status"] for e in last["entries"]] == ["answered", "no_answer"]
    assert last["tally"]["finished"] == 2


def test_it_waits_outside_calling_hours_and_stops_when_paused_or_cancelled(
    world, gateway, monkeypatch
):
    admin = world.client(world.admin_a)
    call_list = make_list(admin, world)
    admin.post(
        reverse("call-list", args=[call_list.pk]), {"action": "start", "consent": "yes"}
    )
    late = timezone.make_aware(datetime.combine(timezone.localdate(), time(22, 0)))
    monkeypatch.setattr(timezone, "now", lambda: late)
    data = admin.post(reverse("call-list-step", args=[call_list.pk])).json()
    assert "between 9 AM and 9 PM" in data["waiting"] and gateway.dispatches == []

    admin.post(reverse("call-list", args=[call_list.pk]), {"action": "pause"})
    calllists.advance_running()
    assert gateway.dispatches == []
    admin.post(reverse("call-list", args=[call_list.pk]), {"action": "cancel"})
    call_list.refresh_from_db()
    assert call_list.status == "cancelled"
    assert set(call_list.entries.values_list("status", flat=True)) == {"skipped"}


def test_a_list_without_an_ai_employee_pauses_instead_of_calling(world, gateway):
    admin = world.client(world.admin_a)
    call_list = make_list(admin, world, numbers="9876543210")
    admin.post(
        reverse("call-list", args=[call_list.pk]), {"action": "start", "consent": "yes"}
    )
    # An older list, made before every call needed an employee.
    CallList.objects.filter(pk=call_list.pk).update(employee=None)
    data = admin.post(reverse("call-list-step", args=[call_list.pk])).json()
    assert data["status"] == "paused" and "AI employee" in data["waiting"]
    assert gateway.dispatches == [] and not CallRecord.objects.exists()
    assert data["entries"][0]["status"] == "waiting"


def test_only_published_employees_are_offered(world):
    from apps.workforce.models import Employee

    draft = Employee.objects.create(
        organisation=world.org_a, name="Draft only", draft=world.employee.draft
    )
    page = body(world.client(world.admin_a).get(reverse("make-calls")))
    assert world.employee.name in page and draft.name not in page
    assert "agent-profile" not in page
    chosen = body(
        world.client(world.admin_a).get(
            reverse("make-calls"), {"speaker": str(world.employee.pk)}
        )
    )
    assert f'value="{world.employee.pk}" selected' in chosen


def test_lists_stay_inside_their_organisation_and_campus(world):
    admin = world.client(world.admin_a)
    call_list = make_list(admin, world)
    url = reverse("call-list", args=[call_list.pk])
    assert world.client(world.admin_b).get(url).status_code == 404
    assert (
        world.client(world.admin_b)
        .post(reverse("call-list-step", args=[call_list.pk]))
        .status_code
        == 404
    )
    staff = world.client(world.staff_north)
    # A whole-organisation list is not campus staff's to see.
    assert staff.get(url).status_code == 404
    # Campus staff must choose one of their campuses.
    response = staff.post(
        reverse("make-calls"),
        {"speaker": str(world.employee.pk), "numbers": "9876543210"},
    )
    assert response.status_code == 200 and "This field is required" in body(response)
    own = make_list(staff, world, campus=str(world.north.pk))
    assert staff.get(reverse("call-list", args=[own.pk])).status_code == 200


def test_the_sidebar_leads_with_the_everyday_steps(world):
    html = body(world.client(world.admin_a).get(reverse("home")))
    nav = html.split('class="side-nav"')[1].split("</nav>")[0]
    everyday = nav.split('class="side-more"')[0]
    for label in ("Home", "AI employees", "Make calls", "Call results", "Settings"):
        assert f">{label}</a>" in everyday, label
    assert ">Leads &amp; contacts</a>" not in everyday
    assert ">Leads &amp; contacts</a>" in nav
    assert "Calls in four steps" in html


def test_without_redis_background_jobs_run_inside_django():
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("REDIS_URL", "CELERY_TASK_ALWAYS_EAGER")
    }
    env["SKIP_ENV_LOCAL"] = "1"
    env["DJANGO_DEBUG"] = "true"
    code = "import config.settings as s; print(s.CELERY_TASK_ALWAYS_EAGER)"
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.stdout.strip() == "True", out.stderr
    env["REDIS_URL"] = "redis://127.0.0.1:6379/9"
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.stdout.strip() == "False", out.stderr


def test_entries_never_show_a_full_number():
    assert CallListEntry._meta.get_field("phone_encrypted").editable is False


# --- what real calling throws at a list ---------------------------------------------------


def _started(admin, world, numbers="Asha, 9876543210\nRavi, 9876543211"):
    call_list = make_list(admin, world, numbers=numbers)
    admin.post(
        reverse("call-list", args=[call_list.pk]), {"action": "start", "consent": "yes"}
    )
    return call_list, reverse("call-list-step", args=[call_list.pk])


def test_a_call_left_active_by_a_stopped_worker_does_not_hold_the_list(
    world, gateway, at_eleven
):
    stuck = CallRecord.objects.create(
        room="stuck-room", masked_number="+91********99", organisation=world.org_a
    )
    CallRecord.objects.filter(pk=stuck.pk).update(
        created_at=at_eleven - timedelta(hours=3)
    )
    admin = world.client(world.admin_a)
    _, step = _started(admin, world)
    admin.post(step)
    stuck.refresh_from_db()
    assert not stuck.is_active and len(gateway.dispatches) == 1


def test_cancelling_during_a_call_still_settles_that_call(world, gateway):
    admin = world.client(world.admin_a)
    call_list, step = _started(admin, world)
    admin.post(step)
    admin.post(reverse("call-list", args=[call_list.pk]), {"action": "cancel"})
    # The page keeps following the call that is still on the line.
    assert "calling/call_list.js" in body(
        admin.get(reverse("call-list", args=[call_list.pk]))
    )
    record = CallRecord.objects.get()
    post_event(Client(), {"room": record.room, "event": "answered"})
    post_event(
        Client(),
        {
            "room": record.room,
            "event": "ended",
            "outcome": "completed",
            "talk_seconds": 12,
        },
    )
    data = admin.post(step).json()
    assert data["status"] == "cancelled" and data["tally"]["calling"] == 0
    assert [e["status"] for e in data["entries"]] == ["answered", "skipped"]
    assert len(gateway.dispatches) == 1


def test_two_steps_at_once_never_work_on_the_same_list(world, gateway, monkeypatch):
    admin = world.client(world.admin_a)
    call_list, step = _started(admin, world)
    lock = f"call-list-step:{call_list.pk}"
    cache.add(lock, True)
    admin.post(step)
    assert gateway.dispatches == [], "another step holds the list"
    cache.delete(lock)
    # The other step marked the entry as calling and is still placing it.
    entry = call_list.entries.order_by("position").first()
    CallListEntry.objects.filter(pk=entry.pk).update(status="calling")
    admin.post(step)
    entry.refresh_from_db()
    assert entry.status == "calling" and gateway.dispatches == []
    # Long after, with still no call, it is given up on.
    later = timezone.now() + calllists.DIAL_GRACE + timedelta(seconds=1)
    monkeypatch.setattr(timezone, "now", lambda: later)
    admin.post(step)
    entry.refresh_from_db()
    assert entry.status == "failed" and "not placed" in entry.reason


def test_campus_staff_see_the_test_calls_they_place(world, gateway):
    world.staff_north.is_staff = True
    world.staff_north.save()
    staff = world.client(world.staff_north)
    made = staff.post(
        reverse("telephony:calls"),
        {"confirm": True, "slot": 0},
        content_type="application/json",
    )
    assert made.status_code == 201, made.content
    call_id = made.json()["call"]["id"]
    assert staff.get(reverse("telephony:call", args=[call_id])).status_code == 200


def test_the_calls_api_refuses_an_employee_it_cannot_use(world, gateway):
    world.admin_a.is_staff = True
    world.admin_a.save()
    admin = world.client(world.admin_a)
    for wanted in ("not-a-uuid", "00000000-0000-0000-0000-000000000000"):
        cache.clear()  # one call start every few seconds per person
        response = admin.post(
            reverse("telephony:calls"),
            {"confirm": True, "slot": 0, "employee": wanted},
            content_type="application/json",
        )
        assert response.status_code == 400, wanted
    assert gateway.dispatches == []
