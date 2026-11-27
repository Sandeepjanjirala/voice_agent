# ruff: noqa: F811
"""Full numbers are visible only through scoped data, while storage stays encrypted."""

import io
import uuid

import pytest
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.utils import timezone

from apps.calling.models import CallList, CallListEntry
from apps.crm import contacts, crypto, importing
from apps.crm.phone_display import UNAVAILABLE
from apps.telephony import calls
from apps.telephony.masking import mask_phone
from apps.telephony.models import CallRecord

from .test_calls import MOBILE, env, gateway  # noqa: F401
from .test_tenancy_crm import ASHA, RAVI, world  # noqa: F401

pytestmark = pytest.mark.django_db


def record_for(world, number=ASHA, **fields):
    return CallRecord.objects.create(
        organisation=world.org_a,
        room=f"display-{uuid.uuid4()}",
        masked_number=mask_phone(number),
        status=CallRecord.Status.FAILED,
        **fields,
    )


def entry_for(world, call, number=ASHA, organisation=None):
    call_list = CallList.objects.create(
        organisation=organisation or world.org_a, name="Example list"
    )
    return CallListEntry.objects.create(
        call_list=call_list,
        position=1,
        phone_encrypted=crypto.encrypt(number),
        phone_lookup=crypto.lookup(number),
        phone_masked=mask_phone(number),
        call=call,
    )


def test_new_outbound_keeps_actual_destination_encrypted(gateway):
    record = calls.start_call()
    record.refresh_from_db()
    assert record.remote_encrypted != MOBILE
    assert crypto.decrypt(record.remote_encrypted) == MOBILE
    assert record.remote_lookup == crypto.lookup(MOBILE)
    assert record.display_number == MOBILE
    assert calls.serialize(record)["number"] == MOBILE
    assert MOBILE not in str(record)


def test_dispatch_error_keeps_recoverable_failed_record(gateway):
    gateway.fail = "create_dispatch"
    with pytest.raises(calls.CallUpstreamError) as raised:
        calls.start_call()
    record = raised.value.record
    assert record.status == CallRecord.Status.FAILED
    assert record.destination_number == MOBILE
    assert MOBILE not in str(raised.value)
    assert MOBILE not in record.failure_reason


def test_contact_and_call_list_show_full_numbers_without_plaintext_storage(world):
    contact = world.contact(world.admin_a, world.north)
    call = record_for(world)
    entry = entry_for(world, call)
    assert contact.display_phone == ASHA
    assert entry.display_phone == ASHA
    assert call.display_number == ASHA
    assert contact.phone_encrypted != ASHA
    assert ASHA not in str(contact)


def test_historical_call_requires_matching_linked_number_and_organisation(world):
    contact = world.contact(world.admin_a, world.north)
    call = record_for(world, contact=contact)
    assert call.destination_number == ASHA
    call.masked_number = mask_phone(RAVI)
    assert call.display_number == UNAVAILABLE
    other_contact = world.contact(world.admin_b, world.west)
    call.masked_number = mask_phone(ASHA)
    call.contact = other_contact
    assert call.display_number == UNAVAILABLE


def test_rehearsal_uses_snapshot_not_lead_contact(world):
    lead = world.lead(world.admin_a, world.north)
    call = record_for(
        world,
        number=MOBILE,
        lead=lead,
        context_snapshot={"rehearsal": True},
        remote_encrypted=crypto.encrypt(MOBILE),
    )
    assert call.destination_number == MOBILE
    call.remote_encrypted = ""
    assert call.destination_number == ""
    assert call.display_number == UNAVAILABLE
    # Even a mask collision is not proof that a lead received a rehearsal.
    call.masked_number = mask_phone(ASHA)
    assert call.destination_number == ""


def test_old_lead_without_live_snapshot_does_not_recover_rehearsal_contact(world):
    lead = world.lead(world.admin_a, world.north)
    call = record_for(world, lead=lead)
    assert call.display_number == UNAVAILABLE
    call.context_snapshot = {"rehearsal": False}
    assert call.destination_number == ASHA


def test_historical_list_recovery_rejects_other_organisation_and_ambiguous_links(world):
    call = record_for(world)
    entry_for(world, call, organisation=world.org_b)
    assert call.display_number == UNAVAILABLE
    entry_for(world, call)
    # A different original number with the same old mask must not be guessed.
    entry_for(world, call, number="+919876599901")
    assert call.display_number == UNAVAILABLE


def test_corrupt_encrypted_snapshot_is_unavailable_without_contact_fallback(world):
    contact = world.contact(world.admin_a, world.north)
    call = record_for(world, contact=contact, remote_encrypted="invalid-ciphertext")
    assert call.display_number == UNAVAILABLE
    contact.phone_encrypted = "invalid-ciphertext"
    assert contact.display_phone == UNAVAILABLE


def test_erased_contact_and_related_copies_stay_erased(world):
    contact = world.contact(world.admin_a, world.north)
    call = record_for(
        world,
        contact=contact,
        remote_encrypted=crypto.encrypt(ASHA),
        remote_lookup=crypto.lookup(ASHA),
    )
    entry = entry_for(world, call)
    contacts.erase(contact)
    call.refresh_from_db()
    entry.refresh_from_db()
    assert contact.display_phone == UNAVAILABLE
    assert call.display_number == UNAVAILABLE
    assert entry.display_phone == UNAVAILABLE
    assert not call.remote_encrypted
    assert not entry.phone_encrypted


def test_previously_erased_contact_cannot_show_call_snapshot(world):
    contact = world.contact(world.admin_a, world.north)
    contact.erased_at = timezone.now()
    contact.save()
    call = record_for(world, contact=contact, remote_encrypted=crypto.encrypt(ASHA))
    assert call.display_number == UNAVAILABLE


def test_import_preview_displays_duplicate_numbers_without_importing_them(world):
    world.contact(world.admin_a, world.north)
    rows = importing.analyse(world.org_a, f"name,phone\nAsha,{ASHA}\nRavi,{RAVI}")
    assert [(row.display_phone, row.status) for row in rows] == [
        (ASHA, "existing"),
        (RAVI, "new"),
    ]
    assert rows[0].number == ""


def test_key_rotation_and_rehash_cover_call_and_list_destinations(world, settings):
    call = record_for(
        world,
        remote_encrypted=crypto.encrypt(ASHA),
        remote_lookup=crypto.lookup(ASHA),
    )
    entry = entry_for(world, call)
    new_key = Fernet.generate_key().decode()
    settings.CONTACT_ENCRYPTION_KEYS = [new_key, *settings.CONTACT_ENCRYPTION_KEYS]
    output = io.StringIO()
    call_command("contactkeys", "rotate", stdout=output)
    settings.CONTACT_ENCRYPTION_KEYS = [new_key]
    settings.CONTACT_LOOKUP_KEY = "new-test-lookup-key-with-more-than-32-characters"
    call_command("contactkeys", "rehash", stdout=output)
    call.refresh_from_db()
    entry.refresh_from_db()
    assert call.display_number == entry.display_phone == ASHA
    assert call.remote_lookup == entry.phone_lookup == crypto.lookup(ASHA)
    assert ASHA not in output.getvalue()
