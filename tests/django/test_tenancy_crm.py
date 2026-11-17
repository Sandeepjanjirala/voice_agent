# ruff: noqa: F811
"""Organisations, campuses and roles; encrypted contacts; leads; conversations;
reports; the access log and retention.

All people and numbers here are made up. The tests put most weight on two
things: nobody reaches another organisation's or campus's records by changing
an ID or a URL, and a full phone number never leaves the database layer.
"""

import csv
import io
import json
import logging
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.crm import contacts, crypto, importing, phones, retention
from apps.crm.models import Contact, ContactImport, FollowUp, Lead, LeadNote, OptOut
from apps.telephony import calls
from apps.telephony.models import CallRecord
from apps.tenancy import access
from apps.tenancy.models import (
    AuditEvent,
    Campus,
    Membership,
    Organisation,
    RetentionPolicy,
)
from apps.workforce import config as employee_config
from apps.workforce import publishing
from apps.workforce.models import Employee
from voice_agent.outbound import parse_dial_request
from voice_agent.profile import parse_profile

from .test_calls import FakeGateway, env, gateway  # noqa: F401

pytestmark = pytest.mark.django_db

User = get_user_model()
ASHA = "+919876500001"
RAVI = "+919876500002"
LATA = "+919876500003"
ALL_NUMBERS = (ASHA, RAVI, LATA)


class World:
    """Two organisations. A has two campuses and a campus-staff member."""

    def __init__(self) -> None:
        self.org_a = Organisation.objects.create(name="Org A")
        self.org_b = Organisation.objects.create(name="Org B")
        self.north = Campus.objects.create(organisation=self.org_a, name="North")
        self.south = Campus.objects.create(organisation=self.org_a, name="South")
        self.west = Campus.objects.create(organisation=self.org_b, name="West")
        self.admin_a = self._member("admin-a", self.org_a, Membership.Role.ORG_ADMIN)
        self.admin_b = self._member("admin-b", self.org_b, Membership.Role.ORG_ADMIN)
        self.staff_north = self._member(
            "staff-north", self.org_a, Membership.Role.CAMPUS_STAFF, [self.north]
        )
        self.root = User.objects.create_superuser("root", password="pw")
        self.outsider = User.objects.create_user("outsider", password="pw")

    @staticmethod
    def _member(username, organisation, role, campuses=()):
        user = User.objects.create_user(username, password="pw")
        membership = Membership.objects.create(
            user=user, organisation=organisation, role=role
        )
        membership.campuses.set(campuses)
        return user

    def client(self, user) -> Client:
        client = Client()
        client.force_login(user)
        return client

    def scope(self, user) -> access.Scope:
        request = type("Request", (), {"user": user, "session": {}})()
        return access.scope_for(request)

    def contact(self, user, campus, number=ASHA, name="Asha Rao", **fields):
        return contacts.create(
            self.scope(user), campus, name=name, phone=number, **fields
        )

    def lead(self, user, campus, number=ASHA, subject="Enquiry", **fields):
        contact = contacts.find(campus.organisation, number) or self.contact(
            user, campus, number
        )
        return Lead.objects.create(
            organisation=campus.organisation,
            campus=campus,
            contact=contact,
            subject=subject,
            **fields,
        )

    def call(self, organisation, campus=None, room=None, **fields):
        fields.setdefault("status", "completed")
        return CallRecord.objects.create(
            organisation=organisation,
            campus=campus,
            room=room or f"room-{CallRecord.objects.count()}",
            masked_number="+91********01",
            **fields,
        )


@pytest.fixture
def world():
    return World()


def body(response) -> str:
    return response.content.decode()


def assert_no_full_number(text: str) -> None:
    for number in ALL_NUMBERS:
        assert number not in text
        assert number[3:] not in text, "the national number leaked"


# --- phone numbers -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "9876500001",
        "09876500001",
        "919876500001",
        "+919876500001",
        "+91 98765 00001",
        "(+91) 98765-00001",
        "0091 9876500001",
        " 98765.00001 ",
    ],
)
def test_one_number_written_many_ways_is_one_number(raw):
    assert phones.normalize(raw) == ASHA


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "12345",
        "1234567890",
        "98765",
        "+91123",
        "+915876500001",
        "call me",
        "+0123456789",
        "98765000011",
    ],
)
def test_bad_numbers_are_refused(raw):
    with pytest.raises(phones.InvalidPhoneError):
        phones.normalize(raw)


def test_other_countries_need_a_plus():
    assert phones.normalize("+1 415 555 0100") == "+14155550100"


# --- encryption ----------------------------------------------------------------


def test_a_number_is_encrypted_and_tampering_is_detected():
    token = crypto.encrypt(ASHA)
    assert ASHA not in token and crypto.decrypt(token) == ASHA
    assert crypto.encrypt(ASHA) != token, "encryption must not be deterministic"
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(token[:-4] + "AAAA")


def test_the_lookup_value_is_keyed_and_stable(settings):
    first = crypto.lookup(ASHA)
    assert first == crypto.lookup(ASHA) and first != crypto.lookup(RAVI)
    assert ASHA not in first and len(first) == 64
    settings.CONTACT_LOOKUP_KEY = "a-different-lookup-key-0123456789abcdef"
    assert crypto.lookup(ASHA) != first


def test_missing_or_bad_keys_are_reported_not_ignored(settings):
    assert crypto.configured()
    settings.CONTACT_ENCRYPTION_KEYS = []
    assert "CONTACT_ENCRYPTION_KEYS" in crypto.problem()
    with pytest.raises(crypto.CryptoError):
        crypto.encrypt(ASHA)
    settings.CONTACT_ENCRYPTION_KEYS = ["not-a-key"]
    assert "not a Fernet key" in crypto.problem()
    settings.CONTACT_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
    settings.CONTACT_LOOKUP_KEY = "short"
    assert "CONTACT_LOOKUP_KEY" in crypto.problem()


def test_key_rotation_keeps_numbers_readable_and_matching(world, settings):
    contact = world.contact(world.admin_a, world.north)
    contacts.opt_out_number(world.org_a, RAVI)
    old_key = settings.CONTACT_ENCRYPTION_KEYS[0]
    new_key = Fernet.generate_key().decode()

    # A new key goes in front: old values still read, new values use the new key.
    settings.CONTACT_ENCRYPTION_KEYS = [new_key, old_key]
    assert crypto.decrypt(contact.phone_encrypted) == ASHA
    assert not crypto.uses_current_key(contact.phone_encrypted)
    out = io.StringIO()
    call_command("contactkeys", "status", stdout=out)
    assert "older key (run rotate): 2" in out.getvalue()

    call_command("contactkeys", "rotate", "--dry-run", stdout=io.StringIO())
    contact.refresh_from_db()
    assert not crypto.uses_current_key(contact.phone_encrypted)
    call_command("contactkeys", "rotate", stdout=io.StringIO())
    contact.refresh_from_db()
    assert crypto.uses_current_key(contact.phone_encrypted)
    assert crypto.uses_current_key(OptOut.objects.get().phone_encrypted)

    # The old key can now be removed; matching was never affected.
    settings.CONTACT_ENCRYPTION_KEYS = [new_key]
    assert crypto.decrypt(contact.phone_encrypted) == ASHA
    assert contacts.find(world.org_a, ASHA) == contact
    assert contacts.is_opted_out(world.org_a, crypto.lookup(RAVI))


def test_rotation_refuses_when_an_old_key_is_missing(world, settings):
    world.contact(world.admin_a, world.north)
    settings.CONTACT_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
    with pytest.raises(CommandError, match="cannot be read"):
        call_command("contactkeys", "status", stdout=io.StringIO())
    with pytest.raises(CommandError, match="Nothing was changed"):
        call_command("contactkeys", "rotate", stdout=io.StringIO())


def test_changing_the_lookup_key_is_repaired_by_rehash(world, settings):
    contact = world.contact(world.admin_a, world.north)
    contacts.opt_out_number(world.org_a, RAVI)
    settings.CONTACT_LOOKUP_KEY = "a-different-lookup-key-0123456789abcdef"
    assert contacts.find(world.org_a, ASHA) is None
    call_command("contactkeys", "rehash", "--dry-run", stdout=io.StringIO())
    assert contacts.find(world.org_a, ASHA) is None
    out = io.StringIO()
    call_command("contactkeys", "rehash", stdout=out)
    assert "2 match values were recomputed" in out.getvalue()
    assert contacts.find(world.org_a, ASHA) == contact
    assert contacts.is_opted_out(world.org_a, crypto.lookup(RAVI))


def test_generate_prints_usable_keys_and_no_existing_secret(settings):
    out = io.StringIO()
    call_command("contactkeys", "generate", stdout=out)
    lines = dict(
        line.split("=", 1) for line in out.getvalue().splitlines() if "=" in line
    )
    Fernet(lines["CONTACT_ENCRYPTION_KEYS"].encode())
    assert len(lines["CONTACT_LOOKUP_KEY"]) >= crypto.MIN_LOOKUP_KEY
    assert settings.CONTACT_LOOKUP_KEY not in out.getvalue()
    assert settings.CONTACT_ENCRYPTION_KEYS[0] not in out.getvalue()


def test_the_database_never_holds_a_number_in_the_clear(world):
    world.contact(world.admin_a, world.north, consent_evidence="Agreed on the form")
    contacts.opt_out_number(world.org_a, RAVI, reason="asked")
    world.lead(world.admin_a, world.north)
    with connection.cursor() as cursor:
        for table in ("crm_contact", "crm_optout", "crm_lead", "tenancy_auditevent"):
            cursor.execute(f"SELECT * FROM {table}")
            dump = json.dumps(cursor.fetchall(), default=str)
            assert_no_full_number(dump)


# --- contacts --------------------------------------------------------------------


def test_a_contact_is_stored_encrypted_matched_and_masked(world):
    contact = world.contact(world.admin_a, world.north)
    assert contact.phone_masked == "+91********01"
    assert contact.phone_lookup == crypto.lookup(ASHA)
    assert crypto.decrypt(contact.phone_encrypted) == ASHA
    assert ASHA not in str(contact) and ASHA not in repr(contact)


def test_duplicates_are_found_across_formats_and_campuses_not_organisations(world):
    first = world.contact(world.admin_a, world.north)
    with pytest.raises(contacts.DuplicateContactError) as error:
        world.contact(world.admin_a, world.south, number="098765 00001")
    assert error.value.contact == first
    assert_no_full_number(str(error.value))
    # Another organisation may hold the same number: nothing is shared.
    other = world.contact(world.admin_b, world.west)
    assert other.organisation == world.org_b
    assert contacts.find(world.org_a, ASHA) == first
    assert contacts.find(world.org_b, ASHA) == other


def test_do_not_call_blocks_everywhere_in_the_organisation(world):
    contact = world.contact(
        world.admin_a,
        world.north,
        consent=Contact.Consent.GRANTED,
        consent_evidence="Agreed on the enquiry form",
    )
    assert contacts.call_block(contact) == ""
    contacts.opt_out(world.scope(world.staff_north), contact, "Asked on a call")
    contact.refresh_from_db()
    assert contact.consent == Contact.Consent.WITHDRAWN
    assert "do-not-call" in contacts.call_block(contact)
    # Opting out twice is harmless; the other organisation is unaffected.
    contacts.opt_out_number(world.org_a, ASHA)
    assert OptOut.objects.filter(organisation=world.org_a).count() == 1
    assert not contacts.is_opted_out(world.org_b, crypto.lookup(ASHA))
    # Recording consent again does not lift the opt-out.
    contacts.record_consent(
        world.scope(world.admin_a), contact, Contact.Consent.GRANTED, "Said yes"
    )
    assert "do-not-call" in contacts.call_block(contact)
    event = AuditEvent.objects.get(action="contact.opted_out")
    assert event.actor_name == "staff-north" and event.campus == world.north


def test_the_number_is_released_only_for_a_permitted_call(world, settings):
    contact = world.contact(world.admin_a, world.north)
    with pytest.raises(contacts.CallNotPermittedError, match="Permission to call"):
        contacts.number_for_call(contact, actor=world.admin_a, purpose="follow-up")
    contacts.record_consent(
        world.scope(world.admin_a), contact, Contact.Consent.GRANTED, "Agreed"
    )
    # Dialling stored contacts is off for this installation by default.
    assert settings.CONTACT_DIALING_ENABLED is False
    with pytest.raises(contacts.CallNotPermittedError, match="switched off"):
        contacts.number_for_call(contact, actor=world.admin_a, purpose="follow-up")
    assert not AuditEvent.objects.filter(action="contact.number_used").exists()

    settings.CONTACT_DIALING_ENABLED = True
    number = contacts.number_for_call(contact, actor=world.admin_a, purpose="follow-up")
    assert number == ASHA
    event = AuditEvent.objects.get(action="contact.number_used")
    assert event.actor_name == "admin-a" and event.detail["purpose"] == "follow-up"
    assert_no_full_number(json.dumps(event.detail))

    contacts.opt_out_number(world.org_a, ASHA)
    with pytest.raises(contacts.CallNotPermittedError, match="do-not-call"):
        contacts.number_for_call(contact, actor=world.admin_a, purpose="follow-up")


def test_erasing_removes_the_person_but_keeps_the_opt_out(world):
    lead = world.lead(world.admin_a, world.north, details="Wants a call on Monday")
    contact = lead.contact
    LeadNote.objects.create(lead=lead, text="Spoke to Asha")
    FollowUp.objects.create(
        lead=lead,
        organisation=world.org_a,
        campus=world.north,
        title="Ring Asha",
        due_at=timezone.now(),
    )
    contacts.opt_out_number(world.org_a, ASHA)
    contacts.erase(contact)
    contact.refresh_from_db()
    lead.refresh_from_db()
    assert contact.is_erased and contact.name == "Erased contact"
    assert contact.phone_encrypted == "" and contact.phone_masked == ""
    assert contact.phone_lookup.startswith("erased:")
    assert lead.stage == Lead.Stage.LOST and lead.details == ""
    assert not lead.notes.exists() and not lead.follow_ups.exists()
    assert "erased" in contacts.call_block(contact)
    # The number stays uncallable, and can be stored again as a new contact.
    assert contacts.is_opted_out(world.org_a, crypto.lookup(ASHA))
    assert world.contact(world.admin_a, world.north).pk != contact.pk


def test_the_audit_log_masks_numbers_in_free_text(world):
    scope = world.scope(world.admin_a)
    from apps.tenancy import audit

    event = audit.record(scope, "test", note=f"called {ASHA} twice", items=[RAVI])
    assert_no_full_number(json.dumps(event.detail))


# --- roles and scope ---------------------------------------------------------------


def test_scopes_follow_roles(world):
    admin = world.scope(world.admin_a)
    assert admin.organisation == world.org_a and admin.all_campuses
    assert admin.can("organisation.manage") and admin.can("contacts.erase")
    staff = world.scope(world.staff_north)
    assert staff.campus_ids == frozenset({world.north.pk})
    assert set(staff.campuses()) == {world.north}
    assert staff.can("leads.edit") and not staff.can("employees.manage")
    assert not staff.can("contacts.export") and not staff.can("audit.view")
    root = world.scope(world.root)
    assert root.role == access.PLATFORM and root.organisation == world.org_a
    assert world.scope(world.outsider) is None


def test_a_campus_taken_out_of_use_leaves_its_staff_with_nothing(world):
    world.north.is_active = False
    world.north.save()
    assert world.scope(world.staff_north).campus_ids == frozenset()
    client = world.client(world.staff_north)
    world.lead(world.admin_a, world.north)
    assert "No leads yet" in body(client.get(reverse("leads")))


def test_the_platform_superuser_works_in_one_organisation_at_a_time(world):
    client = world.client(world.root)
    world.lead(world.admin_a, world.north, subject="A's lead")
    world.lead(world.admin_b, world.west, subject="B's lead")
    html = body(client.get(reverse("leads")))
    assert "A&#x27;s lead" in html and "B&#x27;s lead" not in html
    response = client.post(
        reverse("organisation-switch"), {"organisation": world.org_b.pk}
    )
    assert response.status_code == 302
    html = body(client.get(reverse("leads")))
    assert "B&#x27;s lead" in html and "A&#x27;s lead" not in html
    # Nobody else can switch organisation.
    other = world.client(world.admin_a)
    assert (
        other.post(
            reverse("organisation-switch"), {"organisation": world.org_b.pk}
        ).status_code
        == 403
    )
    assert "B&#x27;s lead" not in body(other.get(reverse("leads")))


def test_setting_up_the_first_organisation_adopts_earlier_records():
    root = User.objects.create_superuser("root", password="pw")
    employee = Employee.objects.create(name="Old", role="r", brief="b" * 30)
    record = CallRecord.objects.create(
        room="old", masked_number="+91********01", status="completed"
    )
    client = Client()
    client.force_login(root)
    # Without an organisation the dashboard sends the superuser to set one up.
    assert client.get(reverse("home"))["Location"] == reverse("organisation-setup")
    assert client.get(reverse("leads"))["Location"] == reverse("organisation-setup")
    response = client.post(
        reverse("organisation-setup"), {"name": "First Org", "campus": "Main"}
    )
    assert response["Location"] == reverse("campuses")
    organisation = Organisation.objects.get()
    assert list(organisation.campuses.values_list("name", flat=True)) == ["Main"]
    assert RetentionPolicy.objects.filter(organisation=organisation).exists()
    employee.refresh_from_db()
    record.refresh_from_db()
    assert employee.organisation == organisation
    assert record.organisation == organisation

    # A second organisation adopts nothing, and a name cannot be reused.
    later = CallRecord.objects.create(
        room="later", masked_number="+91********01", status="completed"
    )
    client.post(reverse("organisation-setup"), {"name": "Second", "campus": "HQ"})
    later.refresh_from_db()
    assert later.organisation is None and Organisation.objects.count() == 2
    again = client.post(
        reverse("organisation-setup"), {"name": "first org", "campus": "X"}
    )
    assert again.status_code == 200 and Organisation.objects.count() == 2


def test_only_the_superuser_creates_organisations(world):
    for user in (world.admin_a, world.staff_north, world.outsider):
        client = world.client(user)
        assert client.get(reverse("organisation-setup")).status_code == 403
        assert (
            client.post(
                reverse("organisation-setup"), {"name": "Mine", "campus": "c"}
            ).status_code
            == 403
        )
    assert Organisation.objects.count() == 2


SCOPED_PAGES = [
    "home",
    "employees",
    "campuses",
    "leads",
    "lead-new",
    "followups",
    "contacts",
    "contact-import",
    "conversations",
    "performance",
    "settings",
]
ADMIN_ONLY_PAGES = [
    "employee-hire",
    "member-add",
    "audit-log",
    "contact-export",
    "conversations-export",
]


@pytest.mark.parametrize("name", SCOPED_PAGES + ADMIN_ONLY_PAGES)
def test_pages_need_a_place_in_an_organisation(world, name, client):
    url = reverse(name)
    anonymous = client.get(url)
    assert anonymous.status_code == 302
    assert anonymous["Location"].startswith(reverse("login"))
    outsider = world.client(world.outsider).get(url)
    if name == "home":
        assert outsider["Location"] == reverse("voice")
    else:
        assert outsider.status_code == 403
    assert world.client(world.admin_a).get(url).status_code == 200
    staff = world.client(world.staff_north).get(url)
    assert staff.status_code == (403 if name in ADMIN_ONLY_PAGES else 200)
    if staff.status_code == 200:
        assert "no-store" in staff["Cache-Control"]


def test_the_sidebar_shows_what_the_role_allows(world):
    admin = body(world.client(world.admin_a).get(reverse("home")))
    staff = body(world.client(world.staff_north).get(reverse("home")))
    assert "Org A" in admin and "Organisation admin" in admin
    assert "Campus staff" in staff
    assert ">Hire an employee</a>" in admin
    assert ">Hire an employee</a>" not in staff
    # Platform tools are for Django staff, which neither of them is.
    for html in (admin, staff):
        assert ">Phone calls</a>" not in html and ">Agent profile</a>" not in html
        assert ">Leads &amp; contacts</a>" in html


# --- campuses and staff --------------------------------------------------------------


def test_admins_manage_campuses_and_staff_only_see_their_own(world):
    admin = world.client(world.admin_a)
    response = admin.post(
        reverse("campuses"),
        {"campus-name": "East", "campus-city": "Vizag", "campus-is_active": "on"},
        follow=True,
    )
    assert "East is added." in body(response)
    assert Campus.objects.filter(organisation=world.org_a, name="East").exists()
    assert AuditEvent.objects.filter(action="campus.created").exists()
    # The same name twice is refused; the other organisation may use it.
    again = admin.post(
        reverse("campuses"), {"campus-name": "east", "campus-is_active": "on"}
    )
    assert "has a campus of this name" in body(again)
    Campus.objects.create(organisation=world.org_b, name="East")

    html = body(admin.get(reverse("campuses")))
    assert "North" in html and "South" in html and "West" not in html
    assert "staff-north" in html and "admin-b" not in html

    staff = world.client(world.staff_north)
    html = body(staff.get(reverse("campuses")))
    assert "North" in html and "South" not in html and "admin-a" not in html
    assert staff.post(reverse("campuses"), {"campus-name": "Mine"}).status_code == 403
    assert staff.get(reverse("campus-edit", args=[world.north.pk])).status_code == 403


def test_a_campus_of_another_organisation_cannot_be_edited(world):
    admin = world.client(world.admin_a)
    url = reverse("campus-edit", args=[world.west.pk])
    assert admin.get(url).status_code == 404
    assert admin.post(url, {"name": "Taken over"}).status_code == 404
    world.west.refresh_from_db()
    assert world.west.name == "West"
    response = admin.post(
        reverse("campus-edit", args=[world.south.pk]),
        {"name": "South Campus", "city": "", "code": ""},
    )
    assert response.status_code == 302
    world.south.refresh_from_db()
    assert world.south.name == "South Campus" and not world.south.is_active


def test_adding_staff_creates_an_ordinary_account_in_this_organisation(world):
    admin = world.client(world.admin_a)
    data = {
        "username": "new-staff",
        "first_name": "Sita",
        "password": "a-long-Unusual-pass-91",
        "role": Membership.Role.CAMPUS_STAFF,
        "campuses": [str(world.south.pk)],
    }
    assert admin.post(reverse("member-add"), data).status_code == 302
    user = User.objects.get(username="new-staff")
    assert not user.is_staff and not user.is_superuser
    assert user.check_password("a-long-Unusual-pass-91")
    membership = user.membership
    assert membership.organisation == world.org_a
    assert list(membership.campuses.all()) == [world.south]
    assert world.scope(user).campus_ids == frozenset({world.south.pk})
    event = AuditEvent.objects.get(action="member.added")
    assert "password" not in json.dumps(event.detail)

    # Campus staff need a campus; another organisation's campus is not a choice;
    # weak passwords and taken usernames are refused.
    for change, message in (
        ({"username": "x1", "campuses": []}, "Choose at least one campus"),
        ({"username": "x2", "campuses": [str(world.west.pk)]}, "not one of the"),
        ({"username": "x3", "password": "12345"}, "too short"),
        ({"username": "ADMIN-B"}, "username is taken"),
    ):
        response = admin.post(reverse("member-add"), {**data, **change})
        assert response.status_code == 200, change
        assert message in body(response), change
    assert not User.objects.filter(username__in=["x1", "x2", "x3"]).exists()


def test_staff_changes_are_scoped_and_an_admin_cannot_lock_themselves_out(world):
    admin = world.client(world.admin_a)
    theirs = Membership.objects.get(user=world.admin_b)
    assert admin.get(reverse("member-edit", args=[theirs.pk])).status_code == 404
    assert (
        admin.post(
            reverse("member-edit", args=[theirs.pk]),
            {"role": Membership.Role.CAMPUS_STAFF},
        ).status_code
        == 404
    )
    own = Membership.objects.get(user=world.admin_a)
    response = admin.post(
        reverse("member-edit", args=[own.pk]),
        {"role": Membership.Role.CAMPUS_STAFF, "campuses": [str(world.north.pk)]},
    )
    assert "cannot remove your own admin role" in body(response)
    own.refresh_from_db()
    assert own.role == Membership.Role.ORG_ADMIN

    staff = Membership.objects.get(user=world.staff_north)
    response = admin.post(
        reverse("member-edit", args=[staff.pk]),
        {
            "role": Membership.Role.CAMPUS_STAFF,
            "campuses": [str(world.south.pk)],
            "is_active": "",
        },
    )
    assert response.status_code == 302
    world.staff_north.refresh_from_db()
    assert not world.staff_north.is_active
    assert list(staff.campuses.all()) == [world.south]


# --- isolation by ID and URL ----------------------------------------------------------


def test_nobody_opens_another_organisations_or_campuss_records(world):
    north = world.lead(world.admin_a, world.north, number=ASHA, subject="North lead")
    south = world.lead(world.admin_a, world.south, number=RAVI, subject="South lead")
    west = world.lead(world.admin_b, world.west, number=LATA, subject="West lead")
    calls_ = {
        "north": world.call(world.org_a, world.north),
        "south": world.call(world.org_a, world.south),
        "whole": world.call(world.org_a),
        "west": world.call(world.org_b, world.west),
    }

    def status(user, name, obj):
        return world.client(user).get(reverse(name, args=[obj.pk])).status_code

    for name, north_obj, south_obj, west_obj in (
        ("lead-detail", north, south, west),
        ("contact-detail", north.contact, south.contact, west.contact),
        ("conversation-detail", calls_["north"], calls_["south"], calls_["west"]),
    ):
        assert status(world.admin_a, name, north_obj) == 200, name
        assert status(world.admin_a, name, south_obj) == 200, name
        assert status(world.admin_a, name, west_obj) == 404, name
        assert status(world.staff_north, name, north_obj) == 200, name
        assert status(world.staff_north, name, south_obj) == 404, name
        assert status(world.staff_north, name, west_obj) == 404, name
        assert status(world.admin_b, name, north_obj) == 404, name
    # A call that belongs to the whole organisation is for its admins.
    assert status(world.admin_a, "conversation-detail", calls_["whole"]) == 200
    assert status(world.staff_north, "conversation-detail", calls_["whole"]) == 404

    # Lists show the same boundaries.
    for user, seen, hidden in (
        (world.admin_a, ("North lead", "South lead"), ("West lead",)),
        (world.staff_north, ("North lead",), ("South lead", "West lead")),
        (world.admin_b, ("West lead",), ("North lead", "South lead")),
    ):
        html = body(world.client(user).get(reverse("leads")))
        assert all(text in html for text in seen), user
        assert not any(text in html for text in hidden), user
    staff = world.client(world.staff_north)
    assert staff.get(reverse("conversations")).context["summary"]["total"] == 1
    assert len(staff.get(reverse("contacts")).context["page"].object_list) == 1


def test_changes_to_records_out_of_scope_are_refused(world):
    south = world.lead(world.admin_a, world.south, number=RAVI)
    west = world.lead(world.admin_b, world.west, number=LATA)
    task = FollowUp.objects.create(
        lead=south,
        organisation=world.org_a,
        campus=world.south,
        title="Ring",
        due_at=timezone.now(),
    )
    call = world.call(world.org_a, world.south)
    staff = world.client(world.staff_north)
    for url, data in (
        (reverse("lead-detail", args=[south.pk]), {"action": "note", "text": "x"}),
        (reverse("lead-detail", args=[west.pk]), {"action": "update", "stage": "won"}),
        (reverse("contact-detail", args=[south.contact.pk]), {"action": "opt_out"}),
        (reverse("contact-detail", args=[west.contact.pk]), {"action": "erase"}),
        (reverse("conversation-detail", args=[call.pk]), {"notes": "x"}),
        (reverse("followups"), {"task": task.pk}),
    ):
        assert staff.post(url, data).status_code == 404, url
    south.refresh_from_db()
    west.refresh_from_db()
    task.refresh_from_db()
    call.refresh_from_db()
    assert not south.notes.exists() and west.stage == Lead.Stage.NEW
    assert task.done_at is None and call.notes == ""
    assert not OptOut.objects.exists() and not west.contact.is_erased


def test_filters_cannot_be_used_to_reach_another_campus(world):
    world.lead(world.admin_a, world.south, number=RAVI, subject="South lead")
    world.lead(world.admin_b, world.west, number=LATA, subject="West lead")
    staff = world.client(world.staff_north)
    for campus in (world.south, world.west):
        for name in ("leads", "contacts", "conversations", "performance"):
            response = staff.get(reverse(name), {"campus": campus.pk})
            assert response.status_code == 200
            html = body(response)
            assert "South lead" not in html and "West lead" not in html
    assert staff.get(reverse("leads"), {"campus": "not-an-id"}).status_code == 200
    # Searching by the full number finds nothing outside the user's scope.
    html = body(staff.get(reverse("leads"), {"q": RAVI}))
    assert "South lead" not in html
    html = body(world.client(world.admin_a).get(reverse("leads"), {"q": "9876500002"}))
    assert "South lead" in html
    html = body(world.client(world.admin_a).get(reverse("leads"), {"q": LATA}))
    assert "West lead" not in html


def test_employees_are_scoped_to_organisation_and_campus(world):
    everywhere = Employee.objects.create(
        organisation=world.org_a, name="Everywhere", role="r", brief="b" * 30
    )
    south_only = Employee.objects.create(
        organisation=world.org_a, name="Southern", role="r", brief="b" * 30
    )
    south_only.campuses.set([world.south])
    theirs = Employee.objects.create(
        organisation=world.org_b, name="Theirs", role="r", brief="b" * 30
    )
    admin = world.client(world.admin_a)
    html = body(admin.get(reverse("employees")))
    assert "Everywhere" in html and "Southern" in html and "Theirs" not in html
    for name in ("employee-detail", "employee-publish", "employee-delete"):
        method = admin.get if name == "employee-detail" else admin.post
        assert method(reverse(name, args=[theirs.pk])).status_code == 404
    assert Employee.objects.filter(pk=theirs.pk).exists()

    staff = world.client(world.staff_north)
    html = body(staff.get(reverse("employees")))
    assert "Everywhere" in html and "Southern" not in html and "Theirs" not in html
    assert (
        staff.get(reverse("employee-detail", args=[everywhere.pk])).status_code == 403
    )

    # Campuses posted for an employee are limited to the organisation's own.
    admin.post(
        reverse("employee-detail", args=[everywhere.pk]),
        {
            "name": "Everywhere",
            "role": "r",
            "channel": "instant",
            "language": "en",
            "campus": [str(world.north.pk), str(world.west.pk), "junk"],
        },
    )
    assert list(everywhere.campuses.all()) == [world.north]
    # An employee cannot be tried in the voice studio from another organisation.
    token = reverse("voice-token")
    assert admin.post(f"{token}?employee={theirs.pk}").status_code == 404


# --- leads -----------------------------------------------------------------------------


def _lead_form(world, **overrides):
    data = {
        "campus": str(world.north.pk),
        "name": "Asha Rao",
        "phone": "98765 00001",
        "subject": "Wants a call back",
        "details": "",
        "origin": Contact.Origin.ENQUIRY,
        "calling_purpose": "Their enquiry",
        "consent": Contact.Consent.GRANTED,
        "consent_evidence": "Ticked the box on the enquiry form, 3 Nov",
        "assigned_to": "",
        "employee": "",
    }
    data.update(overrides)
    return data


def test_adding_a_lead_stores_the_contact_safely(world):
    staff = world.client(world.staff_north)
    response = staff.post(reverse("lead-new"), _lead_form(world), follow=True)
    lead = Lead.objects.get()
    contact = lead.contact
    assert lead.campus == world.north and lead.organisation == world.org_a
    assert contact.consent == Contact.Consent.GRANTED
    assert contact.consent_by == world.staff_north and contact.consent_at
    assert crypto.decrypt(contact.phone_encrypted) == ASHA
    html = body(response)
    assert "Lead added." in html and "+91********01" in html
    assert_no_full_number(html)
    assert {"contact.created", "lead.created"} <= set(
        AuditEvent.objects.values_list("action", flat=True)
    )


def test_a_second_lead_for_a_known_number_joins_the_contact(world):
    staff = world.client(world.staff_north)
    staff.post(reverse("lead-new"), _lead_form(world))
    response = staff.post(
        reverse("lead-new"),
        _lead_form(world, name="A. Rao", subject="Second enquiry"),
        follow=True,
    )
    assert Contact.objects.count() == 1 and Lead.objects.count() == 2
    assert "already belongs to Asha Rao" in body(response)


def test_a_number_held_by_another_campus_is_not_revealed(world):
    world.contact(world.admin_a, world.south, name="Secret Name")
    staff = world.client(world.staff_north)
    response = staff.post(reverse("lead-new"), _lead_form(world))
    html = body(response)
    assert response.status_code == 200 and not Lead.objects.exists()
    assert "another campus" in html and "Secret Name" not in html
    # The same number in another organisation is no obstacle at all.
    other = world.client(world.admin_b)
    assert (
        other.post(
            reverse("lead-new"), _lead_form(world, campus=str(world.west.pk))
        ).status_code
        == 302
    )


@pytest.mark.parametrize(
    "change, message",
    [
        ({"phone": "12345"}, "ten-digit mobile number"),
        ({"consent_evidence": ""}, "how and when the person agreed"),
        ({"campus": "SOUTH"}, "not one of the available choices"),
        ({"campus": "WEST"}, "not one of the available choices"),
        ({"subject": ""}, "This field is required"),
    ],
)
def test_lead_form_refuses_bad_input(world, change, message):
    ids = {"SOUTH": str(world.south.pk), "WEST": str(world.west.pk)}
    change = {key: ids.get(value, value) for key, value in change.items()}
    response = world.client(world.staff_north).post(
        reverse("lead-new"), _lead_form(world, **change)
    )
    assert response.status_code == 200 and message in body(response)
    assert not Contact.objects.exists()


def test_working_a_lead_stage_owner_notes_and_follow_ups(world):
    lead = world.lead(world.admin_a, world.north)
    employee = Employee.objects.create(
        organisation=world.org_a, name="Meera", role="r", brief="b" * 30
    )
    client = world.client(world.staff_north)
    url = reverse("lead-detail", args=[lead.pk])

    client.post(
        url,
        {
            "action": "update",
            "stage": Lead.Stage.INTERESTED,
            "assigned_to": world.staff_north.pk,
            "employee": employee.pk,
        },
    )
    lead.refresh_from_db()
    assert lead.stage == Lead.Stage.INTERESTED and lead.closed_at is None
    assert lead.assigned_to == world.staff_north and lead.employee == employee
    assert AuditEvent.objects.filter(action="lead.assigned").count() == 1
    assert AuditEvent.objects.filter(action="lead.stage").count() == 1

    # Work cannot be given to someone outside the campus or organisation.
    for user in (world.admin_b, world.outsider):
        response = client.post(
            url, {"action": "update", "stage": "new", "assigned_to": user.pk}
        )
        assert "not one of the available choices" in body(response)

    client.post(url, {"action": "note", "text": "Asked to be rung after 5pm"})
    due = timezone.localtime(timezone.now() - timedelta(hours=1))
    client.post(
        url,
        {
            "action": "task",
            "task-title": "Ring back",
            "task-due_at": due.strftime("%Y-%m-%dT%H:%M"),
            "task-assigned_to": world.staff_north.pk,
        },
    )
    task = FollowUp.objects.get()
    assert task.campus == world.north and task.organisation == world.org_a
    html = body(client.get(url))
    assert "Asked to be rung after 5pm" in html and "Ring back" in html

    page = client.get(reverse("followups"))
    assert "Ring back" in body(page) and 'class="overdue"' in body(page)
    assert "1 follow-up due" in body(client.get(reverse("leads")))
    assert client.get(reverse("home")).context["stats"]["tasks_due"] == 1
    client.post(reverse("followups") + "?who=me", {"task": task.pk})
    task.refresh_from_db()
    assert task.done_at and task.done_by == world.staff_north
    assert "Nothing to follow up" in body(client.get(reverse("followups")))

    client.post(url, {"action": "update", "stage": Lead.Stage.WON})
    lead.refresh_from_db()
    assert lead.closed_at is not None and not lead.is_open


def test_lead_filters(world):
    world.lead(world.admin_a, world.north, number=ASHA, subject="Blue widget")
    world.contact(world.admin_a, world.south, number=RAVI, name="Ravi Kumar")
    won = world.lead(world.admin_a, world.south, number=RAVI, subject="Red widget")
    won.stage, won.assigned_to = Lead.Stage.WON, world.admin_a
    won.save()
    client = world.client(world.admin_a)

    def subjects(**params):
        page = client.get(reverse("leads"), params).context["page"]
        return sorted(lead.subject for lead in page)

    assert subjects() == ["Blue widget", "Red widget"]
    assert subjects(stage="won") == ["Red widget"]
    assert subjects(stage="open") == ["Blue widget"]
    assert subjects(assigned="me") == ["Red widget"]
    assert subjects(assigned="none") == ["Blue widget"]
    assert subjects(campus=world.north.pk) == ["Blue widget"]
    assert subjects(q="red") == ["Red widget"]
    assert subjects(q="asha") == ["Blue widget"]
    assert subjects(q="+91 98765 00002") == ["Red widget"]
    assert subjects(q="nothing") == []
    counts = {
        item["value"]: item["count"]
        for item in client.get(reverse("leads")).context["stages"]
    }
    assert counts["new"] == 1 and counts["won"] == 1


# --- contact pages -----------------------------------------------------------------------


def test_opening_a_contact_is_logged_and_shows_no_number(world):
    contact = world.contact(world.admin_a, world.north)
    client = world.client(world.staff_north)
    url = reverse("contact-detail", args=[contact.pk])
    html = body(client.get(url))
    assert "+91********01" in html and "never shown" in html
    assert_no_full_number(html)
    event = AuditEvent.objects.get(action="contact.viewed")
    assert event.actor_name == "staff-north" and event.target_id == str(contact.pk)
    assert event.campus == world.north

    assert "May not be called" in html
    client.post(
        url,
        {
            "action": "consent",
            "consent": Contact.Consent.GRANTED,
            "consent_evidence": "Agreed by phone on 4 Nov",
        },
    )
    assert "May be called" in body(client.get(url))
    refused = client.post(
        url, {"action": "consent", "consent": Contact.Consent.GRANTED}
    )
    assert "how and when the person agreed" in body(refused)

    client.post(url, {"action": "details", "name": "Asha R", "origin": "phone"})
    contact.refresh_from_db()
    assert contact.name == "Asha R" and crypto.decrypt(contact.phone_encrypted) == ASHA

    client.post(url, {"action": "opt_out", "reason": "Asked us to stop"})
    html = body(client.get(url))
    assert "On the list" in html and "May not be called" in html
    assert body(client.get(reverse("contacts"))).count("Do not call") >= 1


def test_only_admins_erase_and_export(world):
    contact = world.contact(world.admin_a, world.north)
    url = reverse("contact-detail", args=[contact.pk])
    staff = world.client(world.staff_north)
    assert staff.post(url, {"action": "erase"}).status_code == 403
    assert staff.get(reverse("contact-export")).status_code == 403
    contact.refresh_from_db()
    assert not contact.is_erased

    admin = world.client(world.admin_a)
    world.contact(world.admin_b, world.west, number=LATA, name="Not Ours")
    export = admin.get(reverse("contact-export"))
    text = body(export)
    assert export["Content-Type"].startswith("text/csv")
    assert "Asha Rao" in text and "Not Ours" not in text
    assert "'+91********01" in text, "a leading + is neutralised for spreadsheets"
    assert_no_full_number(text)
    assert AuditEvent.objects.get(action="contacts.exported").detail == {"rows": 1}

    assert admin.post(url, {"action": "erase"}).status_code == 302
    contact.refresh_from_db()
    assert contact.is_erased
    assert AuditEvent.objects.filter(action="contact.erased").exists()
    assert "Asha Rao" not in body(admin.get(reverse("contacts")))
    # An erased contact can be opened but not edited.
    assert admin.get(url).status_code == 200
    assert admin.post(url, {"action": "details", "name": "Back"}).status_code == 403


def test_a_spreadsheet_formula_in_a_name_is_neutralised(world):
    world.contact(world.admin_a, world.north, name="=HYPERLINK(1)")
    text = body(world.client(world.admin_a).get(reverse("contact-export")))
    assert "'=HYPERLINK(1)" in text


# --- import ----------------------------------------------------------------------------


IMPORT_TEXT = f"""Name,Mobile,Subject
Asha Rao,{ASHA[3:]},First enquiry
Ravi Kumar,+91 98765 00002,
Asha Again,0{ASHA[3:]},Repeat
Bad Number,12345,
,9876500009,No name
Lata Devi,{LATA},Opted out
"""


def _import_form(world, **overrides):
    data = {
        "campus": str(world.north.pk),
        "origin": Contact.Origin.IMPORT,
        "calling_purpose": "Follow-up on their enquiry",
        "text": IMPORT_TEXT,
        "consent_attested": "on",
        "consent_evidence": "Enquiry forms collected at the open day",
        "create_leads": "on",
        "default_subject": "Open-day enquiry",
        "action": "preview",
    }
    data.update(overrides)
    return data


def test_import_checks_every_row_then_stores_only_the_good_ones(world):
    contacts.opt_out_number(world.org_a, LATA)
    existing = world.contact(world.admin_a, world.south, number=RAVI)
    client = world.client(world.staff_north)

    preview = client.post(reverse("contact-import"), _import_form(world))
    assert preview.context["counts"] == {
        "new": 1,
        "invalid": 2,
        "repeated": 1,
        "existing": 1,
        "opted_out": 1,
    }
    html = body(preview)
    assert "Import 1 contact" in html and "+91********01" in html
    assert_no_full_number(html.split("<textarea")[0])
    assert Contact.objects.count() == 1, "a preview stores nothing"

    done = client.post(
        reverse("contact-import"), _import_form(world, action="import"), follow=True
    )
    assert "1 contacts added. 2 duplicates, 2 invalid and 1 on the" in body(done)
    created = Contact.objects.exclude(pk=existing.pk).get()
    assert created.name == "Asha Rao" and created.campus == world.north
    assert created.consent == Contact.Consent.GRANTED
    assert created.consent_evidence.startswith("Enquiry forms")
    assert created.origin == Contact.Origin.IMPORT
    assert crypto.decrypt(created.phone_encrypted) == ASHA
    assert created.leads.get().subject == "First enquiry"
    batch = ContactImport.objects.get()
    assert (batch.rows, batch.created, batch.duplicates) == (6, 1, 2)
    assert (batch.invalid, batch.opted_out) == (2, 1)
    assert batch.created_by == world.staff_north and batch.consent_attested
    assert created.source_import == batch
    event = AuditEvent.objects.get(action="contacts.imported")
    assert event.detail["created"] == 1 and event.campus == world.north


def test_import_without_agreement_stores_contacts_that_cannot_be_called(world):
    client = world.client(world.admin_a)
    client.post(
        reverse("contact-import"),
        _import_form(
            world,
            action="import",
            consent_attested="",
            create_leads="",
            text=IMPORT_TEXT,
        ),
    )
    contact = Contact.objects.get(name="Asha Rao")
    assert contact.consent == Contact.Consent.UNKNOWN and contact.consent_evidence == ""
    assert "Permission to call" in contacts.call_block(contact)
    assert not Lead.objects.exists()


def test_import_accepts_a_file_and_refuses_bad_lists(world):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client = world.client(world.admin_a)
    upload = SimpleUploadedFile(
        "list.csv", ("﻿" + IMPORT_TEXT).encode("utf-8"), content_type="text/csv"
    )
    preview = client.post(
        reverse("contact-import"), _import_form(world, text="", file=upload)
    )
    assert preview.context["counts"]["new"] == 3
    # The checked list is put in the text box, as the file is not sent again.
    assert "Asha Rao" in preview.context["form"]["text"].value()

    for change, message in (
        ({"text": ""}, "Paste the contacts or choose a file"),
        ({"text": "just one line"}, "header line"),
        ({"text": "a,b\n1,2"}, "name the columns"),
        ({"consent_evidence": ""}, "how these people agreed"),
        ({"campus": str(world.west.pk)}, "not one of the available choices"),
        ({"calling_purpose": ""}, "This field is required"),
    ):
        response = client.post(
            reverse("contact-import"), _import_form(world, action="import", **change)
        )
        assert response.status_code == 200 and message in body(response), change
    assert not Contact.objects.exists()
    big = "name,phone\n" + "x,9876500001\n" * (importing.MAX_ROWS + 1)
    with pytest.raises(importing.ImportFormatError, match="At most"):
        importing.analyse(world.org_a, big)


def test_nothing_is_stored_without_keys(world, settings):
    settings.CONTACT_ENCRYPTION_KEYS = []
    client = world.client(world.admin_a)
    for name, data in (
        ("lead-new", _lead_form(world)),
        ("contact-import", _import_form(world, action="import")),
    ):
        response = client.post(reverse(name), data)
        assert response.status_code == 200
        assert "Contacts cannot be stored yet" in body(response)
    assert not Contact.objects.exists()


# --- the do-not-call list in settings -------------------------------------------------------


def test_the_do_not_call_list_is_shared_by_the_organisation(world):
    staff = world.client(world.staff_north)
    response = staff.post(
        reverse("settings"),
        {"action": "opt_out", "dnc-phone": "98765 00003", "dnc-reason": "Rang to ask"},
        follow=True,
    )
    html = body(response)
    assert "+91********03 is on the do-not-call list." in html
    assert_no_full_number(html)
    entry = OptOut.objects.get()
    assert entry.organisation == world.org_a and entry.created_by == world.staff_north
    bad = staff.post(reverse("settings"), {"action": "opt_out", "dnc-phone": "123"})
    assert "ten-digit mobile number" in body(bad)

    # Staff cannot take a number off the list; another organisation cannot see it.
    remove = reverse("optout-remove", args=[entry.pk])
    assert staff.post(remove).status_code == 403
    assert world.client(world.admin_b).post(remove).status_code == 404
    assert "+91********03" not in body(
        world.client(world.admin_b).get(reverse("settings"))
    )
    assert world.client(world.admin_a).post(remove).status_code == 302
    assert not OptOut.objects.exists()
    assert AuditEvent.objects.get(action="optout.removed").detail == {
        "number": "+91********03"
    }


# --- conversations ---------------------------------------------------------------------------


def test_conversations_list_filters_details_and_notes(world):
    now = timezone.now()
    answered = world.call(
        world.org_a,
        world.north,
        answered_at=now,
        duration_seconds=90,
        e2e_latency_p50=1.4,
        placed_by=world.admin_a,
    )
    world.call(world.org_a, world.north, status="no_answer")
    world.call(world.org_a, world.south, created_at=now - timedelta(days=40))
    client = world.client(world.admin_a)

    page = client.get(reverse("conversations"))
    assert page.context["summary"] == {
        "total": 3,
        "answered": 1,
        "answer_rate": 33,
        "talk_minutes": 1.5,
        "average_talk": 90,
        "response": 1.4,
    }

    def total(**params):
        return client.get(reverse("conversations"), params).context["summary"]["total"]

    assert total(status="no_answer") == 1 and total(days="30") == 2
    assert total(campus=world.south.pk) == 1 and total(direction="inbound") == 0
    assert total(status="nonsense", days="x") == 3

    url = reverse("conversation-detail", args=[answered.pk])
    html = body(client.get(url))
    assert "90 seconds" in html and "1.40 s" in html and "+91********01" in html
    client.post(url, {"notes": f"Parent asked to ring {ASHA} instead"})
    answered.refresh_from_db()
    assert answered.notes.startswith("Parent asked")
    assert AuditEvent.objects.filter(action="call.noted").exists()


def test_call_export_is_masked_filtered_scoped_and_logged(world):
    world.call(world.org_a, world.north, status="no_answer")
    world.call(world.org_a, world.south)
    world.call(world.org_b, world.west)
    admin = world.client(world.admin_a)
    rows = list(
        csv.reader(io.StringIO(body(admin.get(reverse("conversations-export")))))
    )
    assert rows[0][2] == "Number (masked)" and len(rows) == 3
    assert {row[5] for row in rows[1:]} == {"North", "South"}
    filtered = admin.get(reverse("conversations-export"), {"status": "no_answer"})
    assert len(list(csv.reader(io.StringIO(body(filtered))))) == 2
    assert AuditEvent.objects.filter(action="calls.exported").count() == 2
    assert (
        world.client(world.staff_north).get(reverse("conversations-export")).status_code
        == 403
    )


def test_a_call_placed_from_the_web_belongs_to_the_callers_organisation(
    world, env, gateway
):
    record = calls.start_call(user=world.admin_a)
    assert record.organisation == world.org_a
    # The platform's own staff, outside any organisation, place unowned calls.
    record.status = "completed"
    record.save()
    assert calls.start_call(user=world.outsider).organisation is None


# --- performance --------------------------------------------------------------------------------


def test_performance_counts_by_campus_and_employee(world):
    now = timezone.now()
    employee = Employee.objects.create(
        organisation=world.org_a, name="Meera", role="Assistant", brief="b" * 30
    )
    won = world.lead(world.admin_a, world.north, number=ASHA, employee=employee)
    won.stage = Lead.Stage.WON
    won.save()
    world.lead(world.admin_a, world.south, number=RAVI)
    world.lead(world.admin_b, world.west, number=LATA)
    world.call(world.org_a, world.north, answered_at=now, duration_seconds=120)
    world.call(world.org_a, world.south, status="failed")
    world.call(world.org_a, world.north, created_at=now - timedelta(days=45))
    world.call(world.org_b, world.west)

    admin = world.client(world.admin_a)
    context = admin.get(reverse("performance")).context
    assert context["summary"]["total"] == 2 and context["summary"]["answered"] == 1
    assert context["lead_total"] == 2 and context["lead_won"] == 1
    rows = {row["name"]: row for row in context["campus_rows"]}
    assert set(rows) == {"North", "South"}
    assert (rows["North"]["leads"], rows["North"]["won"], rows["North"]["total"]) == (
        1,
        1,
        1,
    )
    assert rows["South"]["answer_rate"] == 0 and rows["South"]["open"] == 1
    (employee_row,) = context["employee_rows"]
    assert (employee_row["name"], employee_row["leads"], employee_row["won"]) == (
        "Meera",
        1,
        1,
    )
    assert sum(day["count"] for day in context["by_day"]) == 2
    assert (
        admin.get(reverse("performance"), {"days": "90"}).context["summary"]["total"]
        == 3
    )
    assert admin.get(reverse("performance"), {"days": "abc"}).context["days"] == 30

    staff = world.client(world.staff_north).get(reverse("performance")).context
    assert staff["summary"]["total"] == 1 and staff["lead_total"] == 1
    assert [row["name"] for row in staff["campus_rows"]] == ["North"]

    export = admin.get(reverse("performance"), {"format": "csv"})
    rows = list(csv.reader(io.StringIO(body(export))))
    assert ["Campus", "North", "1", "1", "1", "1", "2.0"] in rows
    assert AuditEvent.objects.filter(action="report.exported").exists()


# --- access log ---------------------------------------------------------------------------------


def test_the_access_log_is_per_organisation_and_for_admins(world):
    contact = world.contact(world.admin_a, world.north)
    world.client(world.staff_north).get(reverse("contact-detail", args=[contact.pk]))
    other = world.contact(world.admin_b, world.west)
    world.client(world.admin_b).get(reverse("contact-detail", args=[other.pk]))

    page = world.client(world.admin_a).get(reverse("audit-log"))
    html = body(page)
    assert "staff-north" in html and "contact.viewed" in html
    assert "admin-b" not in html
    assert_no_full_number(html)
    filtered = world.client(world.admin_a).get(
        reverse("audit-log"), {"action": "contact.viewed", "actor": "staff"}
    )
    assert len(filtered.context["page"].object_list) == 1
    assert world.client(world.staff_north).get(reverse("audit-log")).status_code == 403


def test_numbers_do_not_reach_the_application_log(world, caplog):
    caplog.set_level(logging.DEBUG)
    client = world.client(world.admin_a)
    client.post(reverse("lead-new"), _lead_form(world))
    client.post(reverse("contact-import"), _import_form(world, action="import"))
    client.post(reverse("lead-new"), _lead_form(world, phone="bad"))
    contact = Contact.objects.first()
    contacts.opt_out(world.scope(world.admin_a), contact, "asked")
    assert_no_full_number(caplog.text)


# --- retention ------------------------------------------------------------------------------------


def _age(model, pk, days, *fields):
    when = timezone.now() - timedelta(days=days)
    model.objects.filter(pk=pk).update(**dict.fromkeys(fields, when))


def test_retention_erases_only_what_the_policy_covers(world):
    policy = retention.policy_for(world.org_a)
    assert retention.preview(world.org_a) == {
        "contacts": 0,
        "calls": 0,
        "transcripts": 0,
        "audit": 0,
    }

    old = world.contact(world.admin_a, world.north, number=ASHA, name="Old")
    busy = world.lead(world.admin_a, world.north, number=RAVI).contact
    recent = world.contact(world.admin_a, world.south, number=LATA, name="Recent")
    for contact in (old, busy):
        _age(Contact, contact.pk, 400, "updated_at", "created_at")
    old_call = world.call(world.org_a, world.north)
    _age(CallRecord, old_call.pk, 400, "created_at")
    new_call = world.call(world.org_a, world.north)
    theirs = world.contact(world.admin_b, world.west, number=ASHA, name="Theirs")
    _age(Contact, theirs.pk, 400, "updated_at", "created_at")
    AuditEvent.objects.filter(organisation=world.org_a).update(
        created_at=timezone.now() - timedelta(days=400)
    )
    old_events = AuditEvent.objects.filter(organisation=world.org_a).count()

    # With every period at 0, nothing is due.
    assert retention.preview(world.org_a) == {
        "contacts": 0,
        "calls": 0,
        "transcripts": 0,
        "audit": 0,
    }
    policy.contact_days, policy.call_days, policy.audit_days = 365, 365, 365
    policy.save()
    assert retention.preview(world.org_a) == {
        "contacts": 1,
        "calls": 1,
        "transcripts": 0,
        "audit": old_events,
    }
    out = io.StringIO()
    call_command("retention", stdout=out)
    assert "Org A: would remove 1 contacts, 1 call records" in out.getvalue()
    assert "Nothing was changed" in out.getvalue()
    old.refresh_from_db()
    assert not old.is_erased

    call_command("retention", "--apply", stdout=io.StringIO())
    for contact in (old, busy, recent, theirs):
        contact.refresh_from_db()
    assert old.is_erased
    assert not busy.is_erased, "a contact with an open lead is kept"
    assert not recent.is_erased and not theirs.is_erased
    assert not CallRecord.objects.filter(pk=old_call.pk).exists()
    assert CallRecord.objects.filter(pk=new_call.pk).exists()
    applied = AuditEvent.objects.get(organisation=world.org_a)
    assert applied.action == "retention.applied" and applied.actor_name == "system"
    assert applied.detail["contacts"] == 1


def test_only_admins_set_and_apply_retention(world):
    staff = world.client(world.staff_north)
    for action in ("retention", "retention_apply", "organisation"):
        assert staff.post(reverse("settings"), {"action": action}).status_code == 403
    assert "managed by an organisation admin" in body(staff.get(reverse("settings")))

    admin = world.client(world.admin_a)
    response = admin.post(
        reverse("settings"),
        {
            "action": "retention",
            "keep-contact_days": "30",
            "keep-call_days": "0",
            "keep-transcript_days": "30",
            "keep-audit_days": "0",
        },
        follow=True,
    )
    assert "Retention policy saved." in body(response)
    assert retention.policy_for(world.org_a).contact_days == 30
    assert retention.policy_for(world.org_b).contact_days == 0
    contact = world.contact(world.admin_a, world.north)
    _age(Contact, contact.pk, 60, "updated_at")
    assert "would erase 1 contact " in body(admin.get(reverse("settings")))
    response = admin.post(
        reverse("settings"), {"action": "retention_apply"}, follow=True
    )
    assert "Retention applied: 1 contacts erased" in body(response)
    assert AuditEvent.objects.get(action="retention.applied").actor_name == "admin-a"

    admin.post(
        reverse("settings"),
        {"action": "organisation", "org-name": "Org A2", "org-transcripts": "keep"},
    )
    world.org_a.refresh_from_db()
    assert world.org_a.name == "Org A2" and world.org_a.transcripts == "keep"


# --- phone test with a published employee version ---------------------------------------


SCRIPT = {
    "agent_name": "Meera",
    "role": "Enquiry assistant",
    "greeting": "Hello, I am Meera, an AI assistant. Is this a good time?",
    "purpose": "Follow up on the person's enquiry.",
    "steps": [{"name": "Ask", "guidance": "Ask what they need."}],
    "limits": "You cannot confirm anything.",
    "ending": "End politely.",
}


def _employee(world, organisation=None, **overrides) -> Employee:
    fields = {
        "organisation": organisation or world.org_a,
        "name": "Meera",
        "role": "Enquiry assistant",
        "brief": "Follow up on enquiries and note what people need.",
        "draft": employee_config.normalize(SCRIPT),
        **overrides,
    }
    return Employee.objects.create(**fields)


def test_a_phone_test_uses_the_published_version_and_records_it(world, gateway):
    employee = _employee(world)
    employee.campuses.set([world.north])
    first = publishing.publish(employee, world.admin_a)
    # Later edits to the draft must not reach the call.
    employee.draft = employee_config.normalize(
        {**SCRIPT, "greeting": "An unpublished greeting."}
    )
    employee.save()
    admin = world.client(world.admin_a)

    page = body(admin.get(reverse("employee-detail", args=[employee.pk])))
    assert "Call the test number" in page and "+91********10" in page
    assert "+919876543210" not in page

    response = admin.post(reverse("employee-test-call", args=[employee.pk]))
    record = CallRecord.objects.get()
    assert response["Location"] == reverse("conversation-detail", args=[record.pk])
    assert record.employee_version == first and record.organisation == world.org_a
    assert record.campus == world.north and record.placed_by == world.admin_a
    assert record.masked_number == "+91********10"

    # What the voice worker receives: this version's script, and only the
    # approved number to dial.
    (dispatch,) = gateway.dispatches
    profile = parse_profile(dispatch.metadata)
    assert profile.greeting == SCRIPT["greeting"] and profile.agent_name == "Meera"
    assert parse_dial_request(dispatch.metadata).to == "+919876543210"

    detail = admin.get(reverse("conversation-detail", args=[record.pk]))
    html = body(detail)
    assert "Meera, version 1" in html and "This call is in progress" in html
    assert 'http-equiv="refresh"' in html and "End call" in html
    rows = admin.get(reverse("performance")).context["employee_rows"]
    assert rows[0]["name"] == "Meera" and rows[0]["total"] == 1

    # Only one call at a time, as before.
    from django.core.cache import cache

    cache.clear()
    again = admin.post(reverse("employee-test-call", args=[employee.pk]), follow=True)
    assert "No call was placed." in body(again)
    assert CallRecord.objects.count() == 1

    ended = admin.post(
        reverse("conversation-detail", args=[record.pk]),
        {"action": "hang_up"},
        follow=True,
    )
    assert "The call was ended." in body(ended)
    assert gateway.deleted == [record.room]
    record.refresh_from_db()
    assert not record.is_active
    assert AuditEvent.objects.filter(action="call.ended").exists()


def test_a_draft_or_paused_employee_cannot_be_put_on_a_call(world, gateway):
    employee = _employee(world)
    admin = world.client(world.admin_a)
    url = reverse("employee-test-call", args=[employee.pk])
    response = admin.post(url, follow=True)
    assert "has not been published yet" in body(response)

    publishing.publish(employee, world.admin_a)
    employee.taking_calls = False
    employee.save()
    response = admin.post(url, follow=True)
    assert "is paused" in body(response)
    assert "switch the employee on" in body(
        admin.get(reverse("employee-detail", args=[employee.pk]))
    )
    assert not CallRecord.objects.exists() and gateway.dispatches == []


def test_phone_tests_are_for_this_organisations_admins_only(world, gateway):
    employee = _employee(world)
    publishing.publish(employee, world.admin_a)
    url = reverse("employee-test-call", args=[employee.pk])
    assert world.client(world.staff_north).post(url).status_code == 403
    assert world.client(world.admin_b).post(url).status_code == 404
    assert world.client(world.outsider).post(url).status_code == 403
    assert world.client(world.admin_a).get(url).status_code == 405
    assert not CallRecord.objects.exists() and gateway.dispatches == []


def test_a_phone_test_reports_why_it_cannot_start(world, env):
    employee = _employee(world)
    publishing.publish(employee, world.admin_a)
    env.delenv("OUTBOUND_TEST_ALLOWED_NUMBER")
    admin = world.client(world.admin_a)
    page = body(admin.get(reverse("employee-detail", args=[employee.pk])))
    assert "Phone test is not available" in page
    assert "Call the test number" not in page
    response = admin.post(
        reverse("employee-test-call", args=[employee.pk]), follow=True
    )
    assert "No call was placed." in body(response)
    assert not CallRecord.objects.exists()


def test_only_whoever_may_end_a_call_can_end_it(world, gateway):
    employee = _employee(world)
    employee.campuses.set([world.north])
    publishing.publish(employee, world.admin_a)
    world.client(world.admin_a).post(reverse("employee-test-call", args=[employee.pk]))
    record = CallRecord.objects.get()
    staff = world.client(world.staff_north)
    url = reverse("conversation-detail", args=[record.pk])
    assert "End call" not in body(staff.get(url))
    assert staff.post(url, {"action": "hang_up"}).status_code == 403
    assert (
        world.client(world.admin_b).post(url, {"action": "hang_up"}).status_code == 404
    )
    assert gateway.deleted == []
