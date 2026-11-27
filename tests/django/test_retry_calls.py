"""Retry tests use a fake LiveKit gateway; no phone call leaves the suite."""

# Imported fixtures are intentionally named as test parameters.
# ruff: noqa: F811

import json
from datetime import datetime, time

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.calling import calllists, retries
from apps.calling.models import CallAttempt, CallList, CallRetry
from apps.crm import contacts, crypto
from apps.crm.models import Contact
from apps.telephony import calls
from apps.telephony.models import CallRecord
from apps.workforce import publishing

from .test_call_lists import at_eleven, make_list, world  # noqa: F401
from .test_calling import consented
from .test_calls import env, gateway  # noqa: F401

pytestmark = pytest.mark.django_db
NUMBER = "+919876543210"


def failed_list(world, gateway, *, with_record=True, status="failed"):
    client = world.client(world.admin_a)
    original = make_list(client, world, numbers=f"Asha, {NUMBER}")
    client.post(
        reverse("call-list", args=[original.pk]), {"action": "start", "consent": "yes"}
    )
    entry = original.entries.get()
    if with_record:
        client.post(reverse("call-list-step", args=[original.pk]))
        entry.refresh_from_db()
        CallRecord.objects.filter(pk=entry.call_id).update(
            status=status, ended_at=timezone.now()
        )
    entry.status = "no_answer" if status in ("no_answer", "rejected") else "failed"
    entry.reason = "Previous attempt failed"
    entry.save()
    original.refresh_from_db()
    original.status = CallList.Status.DONE
    original.save(update_fields=["status"])
    entry.refresh_from_db()
    return client, original, entry


def entry_url(entry):
    return reverse("call-list-retry", args=[entry.call_list_id, entry.pk])


@pytest.mark.parametrize("status", ["failed", "no_answer", "rejected"])
def test_failed_list_retry_keeps_original_and_repeated_posts_reuse_one_list(
    world, gateway, status
):
    client, original, entry = failed_list(world, gateway, status=status)
    source_call = entry.call
    old_snapshot = source_call.context_snapshot
    first = client.post(entry_url(entry))
    second = client.post(entry_url(entry))
    assert first.status_code == 302 and first["Location"] == second["Location"]
    assert CallRetry.objects.count() == 1
    retry = CallRetry.objects.get()
    new_list = retry.target_list
    assert new_list.pk != original.pk and new_list.status == "running"
    assert new_list.consent_confirmed_at == original.consent_confirmed_at
    assert (
        new_list.employee == original.employee and new_list.purpose == original.purpose
    )
    assert crypto.decrypt(new_list.entries.get().phone_encrypted) == NUMBER
    assert len(gateway.dispatches) == 1  # A retry queues only its single number.
    client.post(reverse("call-list-step", args=[new_list.pk]))
    assert len(gateway.dispatches) == 2
    new_call = new_list.entries.get().call
    assert new_call.pk != source_call.pk
    assert new_call.context_snapshot["profile"] == old_snapshot["profile"]
    source_call.refresh_from_db()
    entry.refresh_from_db()
    assert source_call.status == status and source_call.context_snapshot == old_snapshot
    assert entry.call_id == source_call.pk


def test_retry_without_record_needs_no_reentry_of_number(world, gateway):
    client, _original, entry = failed_list(world, gateway, with_record=False)
    client.post(entry_url(entry))
    new_list = CallRetry.objects.get().target_list
    client.post(reverse("call-list-step", args=[new_list.pk]))
    assert len(gateway.dispatches) == 1
    assert json.loads(gateway.dispatches[0].metadata)["outbound_call"]["to"] == NUMBER
    entry.refresh_from_db()
    assert entry.call_id is None and entry.status == "failed"


def test_whole_organisation_list_can_retry_employee_assigned_to_locations(
    world, gateway
):
    world.employee.campuses.add(world.north)
    client, original, entry = failed_list(world, gateway, with_record=False)
    assert original.campus_id is None
    options = retries.entry_options(world.scope(world.admin_a), entry)
    assert options["can_retry"]
    client.post(entry_url(entry))
    queued = CallRetry.objects.get().target_list
    calllists.advance(queued, gateway=gateway)
    assert len(gateway.dispatches) == 1


def test_conversation_and_list_buttons_share_the_same_retry(world, gateway):
    client, _original, entry = failed_list(world, gateway)
    first = client.post(reverse("conversation-retry", args=[entry.call_id]))
    second = client.post(entry_url(entry))
    assert first["Location"] == second["Location"]
    assert CallRetry.objects.count() == 1


def test_retry_requires_post_csrf_and_scope(world, gateway):
    client, _original, entry = failed_list(world, gateway)
    assert client.get(entry_url(entry)).status_code == 405
    assert world.client(world.admin_b).post(entry_url(entry)).status_code == 404
    assert world.client(world.staff_north).post(entry_url(entry)).status_code == 404
    protected = Client(enforce_csrf_checks=True)
    protected.force_login(world.admin_a)
    assert protected.post(entry_url(entry)).status_code == 403
    assert not CallRetry.objects.exists()


@pytest.mark.parametrize(
    "change", ["optout", "consent", "paused", "completed", "active"]
)
def test_list_retry_refuses_disallowed_attempts(world, gateway, change):
    client, original, entry = failed_list(world, gateway)
    if change == "optout":
        contacts.opt_out_number(world.org_a, NUMBER)
    elif change == "consent":
        original.consent_confirmed_at = None
        original.save()
    elif change == "paused":
        world.employee.taking_calls = False
        world.employee.save()
    elif change == "completed":
        CallRecord.objects.filter(pk=entry.call_id).update(status="completed")
    else:
        CallRecord.objects.filter(pk=entry.call_id).update(status="ringing")
    client.post(entry_url(entry))
    assert not CallRetry.objects.exists() and len(gateway.dispatches) == 1


def test_queued_retry_rechecks_optout_before_dial(world, gateway):
    client, _original, entry = failed_list(world, gateway)
    client.post(entry_url(entry))
    contacts.opt_out_number(world.org_a, NUMBER)
    retry_list = CallRetry.objects.get().target_list
    calllists.advance(retry_list, gateway=gateway)
    assert retry_list.entries.get().status == "skipped"
    assert len(gateway.dispatches) == 1


@pytest.mark.parametrize("limit", ["hours", "budget"])
def test_queued_retry_obeys_hours_and_budget(world, gateway, monkeypatch, limit):
    client, _original, entry = failed_list(world, gateway)
    client.post(entry_url(entry))
    if limit == "hours":
        late = timezone.make_aware(datetime.combine(timezone.localdate(), time(22)))
        monkeypatch.setattr(timezone, "now", lambda: late)
    else:
        world.org_a.monthly_call_minutes = 1
        world.org_a.save()
        CallRecord.objects.filter(pk=entry.call_id).update(duration_seconds=60)
    retry_list = CallRetry.objects.get().target_list
    calllists.advance(retry_list, gateway=gateway)
    retry_list.refresh_from_db()
    assert retry_list.waiting and len(gateway.dispatches) == 1


def failed_contact(world, *, test=False):
    contact = consented(world, world.north, NUMBER)
    plan = publishing.plan_call(world.employee, campus=world.north, rehearsal=test)
    record = CallRecord.objects.create(
        organisation=world.org_a,
        campus=world.north,
        placed_by=world.admin_a,
        room="outcall-original",
        status="failed",
        masked_number="+91********10",
        remote_encrypted=crypto.encrypt(NUMBER),
        remote_lookup=crypto.lookup(NUMBER),
        contact=None if test else contact,
        employee_version=plan.version,
        context_snapshot=plan.snapshot,
    )
    return record, contact


def test_contact_retry_keeps_destination_context_and_history(world, gateway, settings):
    settings.CONTACT_DIALING_ENABLED = True
    original, contact = failed_contact(world)
    client = world.client(world.admin_a)
    first = client.post(reverse("conversation-retry", args=[original.pk]))
    second = client.post(reverse("conversation-retry", args=[original.pk]))
    retry = CallRetry.objects.get()
    assert first["Location"] == second["Location"]
    assert retry.target_call.contact == contact
    assert retry.target_call.destination_number == NUMBER
    assert (
        retry.target_call.context_snapshot["profile"]
        == original.context_snapshot["profile"]
    )
    assert len(gateway.dispatches) == 1


@pytest.mark.parametrize("change", ["consent", "optout", "number", "budget", "switch"])
def test_contact_retry_rechecks_current_permission(world, gateway, settings, change):
    settings.CONTACT_DIALING_ENABLED = True
    original, contact = failed_contact(world)
    if change == "consent":
        contact.consent = Contact.Consent.UNKNOWN
        contact.save()
    elif change == "optout":
        contacts.opt_out_number(world.org_a, NUMBER)
    elif change == "number":
        contact.phone_encrypted = crypto.encrypt("+919876543211")
        contact.save()
    elif change == "budget":
        world.org_a.monthly_call_minutes = 1
        world.org_a.save()
        original.duration_seconds = 60
        original.save()
    else:
        settings.CONTACT_DIALING_ENABLED = False
    client = world.client(world.admin_a)
    client.post(reverse("conversation-retry", args=[original.pk]))
    assert not CallRetry.objects.exists() and gateway.dispatches == []


def test_retry_of_failed_test_retry_keeps_allowlist_policy(world, gateway, env):
    original, _contact = failed_contact(world, test=True)
    scope = world.scope(world.admin_a)
    first = retries.retry_call(scope, original, gateway=gateway).target_call
    assert CallAttempt.objects.filter(call=first, mode="test").exists()
    CallRecord.objects.filter(pk=first.pk).update(status="failed")
    first.refresh_from_db()
    second = retries.retry_call(scope, first, gateway=gateway).target_call
    assert second.pk != first.pk and len(gateway.dispatches) == 2
    CallRecord.objects.filter(pk=second.pk).update(status="failed")
    second.refresh_from_db()
    env.setenv("OUTBOUND_TEST_ALLOWED_NUMBER", "+919876543211")
    with pytest.raises(calls.CallError, match="Test calls"):
        retries.retry_call(scope, second, gateway=gateway)
    assert len(gateway.dispatches) == 2


def test_dispatch_failure_keeps_new_failed_record_and_duplicate_posts_dont_dial(
    world, gateway
):
    original, _contact = failed_contact(world, test=True)
    gateway.fail = "create_dispatch"
    scope = world.scope(world.admin_a)
    first = retries.retry_call(scope, original, gateway=gateway)
    second = retries.retry_call(scope, original, gateway=gateway)
    assert first.pk == second.pk and first.target_call.status == "failed"
    assert CallRecord.objects.count() == 2


def test_missing_original_number_is_explained_not_guessed(world, gateway):
    original, _contact = failed_contact(world, test=True)
    original.remote_encrypted = ""
    original.remote_lookup = ""
    original.save()
    options = retries.call_options(world.scope(world.admin_a), original)
    assert not options["can_retry"] and "not saved" in options["retry_block"]
    assert gateway.dispatches == []


def test_reading_retry_options_never_dials(world, gateway):
    original, _contact = failed_contact(world, test=True)
    response = world.client(world.admin_a).get(
        reverse("conversation-detail", args=[original.pk])
    )
    assert response.status_code == 200
    assert not CallRetry.objects.exists() and gateway.dispatches == []
    cache.clear()


def test_retry_refreshes_privacy_flags_but_keeps_original_script(world, gateway):
    world.org_a.transcripts = "on"
    world.org_a.cloud_observability = True
    world.org_a.save()
    world.employee.organisation = world.org_a
    original, _contact = failed_contact(world, test=True)
    old_profile = original.context_snapshot["profile"]
    assert old_profile.get("transcript") and old_profile.get("observability")
    world.org_a.transcripts = "off"
    world.org_a.cloud_observability = False
    world.org_a.save()
    original = CallRecord.objects.get(pk=original.pk)
    retry = retries.retry_call(world.scope(world.admin_a), original, gateway=gateway)
    profile = retry.target_call.context_snapshot["profile"]
    assert not profile.get("transcript") and not profile.get("observability")
    assert profile["purpose"] == old_profile["purpose"]


def test_list_dispatch_failure_links_record_so_either_retry_button_works(
    world, gateway
):
    client, _original, entry = failed_list(world, gateway, with_record=False)
    client.post(entry_url(entry))
    queued = CallRetry.objects.get().target_list
    gateway.fail = "create_dispatch"
    calllists.advance(queued, gateway=gateway)
    failed = queued.entries.get()
    assert failed.status == "failed" and failed.call_id
    assert failed.call.status == "failed"
    response = client.post(reverse("conversation-retry", args=[failed.call_id]))
    next_retry = CallRetry.objects.get(source_entry=failed)
    assert response["Location"] == reverse(
        "call-list", args=[next_retry.target_list_id]
    )


def test_duplicate_during_dispatch_opens_existing_attempt(world, gateway, monkeypatch):
    original, _contact = failed_contact(world, test=True)
    scope = world.scope(world.admin_a)
    duplicates = []
    original_dispatch = gateway.dispatch

    async def concurrent_post(request):
        from asgiref.sync import sync_to_async

        duplicate = await sync_to_async(retries.retry_call)(
            scope, original, gateway=gateway
        )
        duplicates.append(duplicate)
        await original_dispatch(request)

    monkeypatch.setattr(gateway, "dispatch", concurrent_post)
    first = retries.retry_call(scope, original, gateway=gateway)
    assert duplicates[0].target_call_id == first.target_call_id
    assert CallRetry.objects.count() == 1 and len(gateway.dispatches) == 1
