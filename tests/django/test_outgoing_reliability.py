"""Outgoing queue regressions use the fake gateway; no calls leave these tests."""

# ruff: noqa: F811

import csv
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.calling import calllists, dialer
from apps.calling.models import CallList, CallListEntry
from apps.telephony import calls
from apps.telephony.models import CallRecord
from apps.tenancy.models import AuditEvent
from apps.workforce import publishing

from .test_call_lists import _started, at_eleven, make_list, world  # noqa: F401
from .test_calls import env, gateway  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("state", ["running", "paused", "cancelled"])
def test_lists_settle_their_own_stale_call_when_status_checks_fail(
    world, gateway, at_eleven, state
):
    client = world.client(world.admin_a)
    call_list, step = _started(client, world, numbers="9876543210")
    client.post(step)
    CallList.objects.filter(pk=call_list.pk).update(status=state)
    record = CallRecord.objects.get()
    CallRecord.objects.filter(pk=record.pk).update(
        created_at=at_eleven - timedelta(hours=3)
    )
    gateway.fail = "call_state"

    response = client.post(step).json()

    assert response["tally"]["calling"] == 0
    assert response["entries"][0]["status"] == "failed"
    assert response["status"] == ("done" if state == "running" else state)
    assert len(gateway.dispatches) == 1


@pytest.mark.parametrize("reason", ["", "Another call is on the line."])
def test_pause_while_checking_the_line_prevents_the_next_call(
    world, gateway, monkeypatch, reason
):
    client = world.client(world.admin_a)
    call_list, step = _started(client, world)

    def paused_while_checking(*args):
        client.post(reverse("call-list", args=[call_list.pk]), {"action": "pause"})
        return reason

    monkeypatch.setattr(calllists, "_wait_reason", paused_while_checking)
    response = client.post(step).json()

    assert response["status"] == "paused"
    assert response["waiting"] == "Paused by you."
    assert response["tally"]["waiting"] == 2
    assert not gateway.dispatches


def test_cancel_during_preparation_is_not_undone_by_a_dial_error(
    world, gateway, monkeypatch
):
    client = world.client(world.admin_a)
    call_list, step = _started(client, world)

    def cancelled_before_plan(*args, **kwargs):
        client.post(reverse("call-list", args=[call_list.pk]), {"action": "cancel"})
        raise publishing.PublishError(["The AI employee was paused."])

    monkeypatch.setattr(publishing, "plan_call", cancelled_before_plan)
    response = client.post(step).json()

    assert response["status"] == "cancelled"
    assert response["tally"]["skipped"] == 2
    assert not gateway.dispatches


def test_employee_location_mismatch_is_rejected_before_preparing_a_list(world):
    world.employee.campuses.add(world.north)
    client = world.client(world.admin_a)
    response = client.post(
        reverse("make-calls"),
        {
            "speaker": str(world.employee.pk),
            "campus": str(world.south.pk),
            "numbers": "9876543210",
        },
    )

    assert response.status_code == 200
    assert "not assigned to this location" in response.content.decode()
    assert not CallList.objects.exists()


def test_employee_reassignment_pauses_a_prepared_list_before_dial(world, gateway):
    world.employee.campuses.add(world.north)
    client = world.client(world.admin_a)
    call_list = make_list(client, world, campus=str(world.north.pk))
    world.employee.campuses.set([world.south])
    client.post(
        reverse("call-list", args=[call_list.pk]), {"action": "start", "consent": "yes"}
    )
    response = client.post(reverse("call-list-step", args=[call_list.pk])).json()

    assert response["status"] == "paused"
    assert "no longer assigned" in response["waiting"]
    assert response["entries"][0]["status"] == CallListEntry.Status.WAITING
    assert not gateway.dispatches


def test_unreadable_csv_shows_a_form_error_instead_of_crashing(world):
    client = world.client(world.admin_a)
    response = client.post(
        reverse("make-calls"),
        {
            "speaker": str(world.employee.pk),
            "upload": SimpleUploadedFile(
                "contacts.csv",
                ("name,phone\n" + "a" * (csv.field_size_limit() + 1)).encode(),
            ),
        },
    )

    assert response.status_code == 200
    assert "CSV file could not be read" in response.content.decode()
    assert not CallList.objects.exists()


def test_number_inside_decimal_text_is_not_silently_changed():
    upload = SimpleUploadedFile("contacts.csv", b"phone\n987.06543210\n9876543211.0")
    parsed = calllists.parse(upload=upload)
    assert parsed.rows == [("", "+919876543211")]
    assert len(parsed.rejected) == 1


def test_enormous_test_slot_is_a_validation_error(env, gateway):
    with pytest.raises(calls.CallError, match="Choose one"):
        dialer.test_destination("9" * 5000)


def test_repeated_list_actions_redirect_without_changing_the_action_twice(world):
    client = world.client(world.admin_a)
    call_list = make_list(client, world)
    url = reverse("call-list", args=[call_list.pk])
    for action in ("start", "pause", "resume", "cancel"):
        data = {"action": action, "consent": "yes"}
        assert client.post(url, data).status_code == 302
        assert client.post(url, data).status_code == 302
    assert AuditEvent.objects.filter(action="call_list.started").count() == 1
    assert AuditEvent.objects.filter(action="call_list.paused").count() == 1
    assert AuditEvent.objects.filter(action="call_list.resumed").count() == 1
    assert AuditEvent.objects.filter(action="call_list.cancelled").count() == 1

    # A stale Start submission must never revive a cancelled list.
    assert client.post(url, {"action": "start", "consent": "yes"}).status_code == 302
    call_list.refresh_from_db()
    assert call_list.status == "cancelled"
