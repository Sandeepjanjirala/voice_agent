"""The only code that creates, matches, opts out, decrypts or erases a contact.

Full numbers exist in memory here and nowhere else in the dashboard: they are
never returned to a view for display, written to a log, or put in the audit
trail.
"""

import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.telephony.masking import mask_phone
from apps.tenancy import audit

from . import crypto
from .models import Contact, Lead, OptOut
from .phones import normalize


class DuplicateContactError(Exception):
    """The organisation already has a contact with this number."""

    def __init__(self, contact: Contact) -> None:
        super().__init__("A contact with this number already exists.")
        self.contact = contact


class CallNotPermittedError(Exception):
    """This contact may not be called. The message says why, without a number."""


def find(organisation, number: str) -> Contact | None:
    """The organisation's contact with this (normalised) number, in any campus."""
    return Contact.objects.filter(
        organisation=organisation, phone_lookup=crypto.lookup(number)
    ).first()


def is_opted_out(organisation, lookup: str) -> bool:
    return OptOut.objects.filter(
        organisation=organisation, phone_lookup=lookup
    ).exists()


def create(scope, campus, *, name: str, phone: str, **fields) -> Contact:
    """Store a new contact. `phone` may be written any common way.
    Raises InvalidPhoneError or DuplicateContactError."""
    number = normalize(phone)
    lookup = crypto.lookup(number)
    existing = Contact.objects.filter(
        organisation=scope.organisation, phone_lookup=lookup
    ).first()
    if existing:
        raise DuplicateContactError(existing)
    if fields.get("consent", Contact.Consent.UNKNOWN) != Contact.Consent.UNKNOWN:
        fields.setdefault("consent_at", timezone.now())
        fields.setdefault("consent_by", scope.user)
    try:
        with transaction.atomic():
            contact = Contact.objects.create(
                organisation=scope.organisation,
                campus=campus,
                name=name,
                phone_encrypted=crypto.encrypt(number),
                phone_lookup=lookup,
                phone_masked=mask_phone(number),
                created_by=scope.user,
                **fields,
            )
    except IntegrityError:
        # Two people added the same number at once.
        raise DuplicateContactError(find(scope.organisation, number)) from None
    audit.record(scope, "contact.created", contact, origin=contact.origin)
    return contact


def record_consent(scope, contact: Contact, consent: str, evidence: str) -> None:
    contact.consent = consent
    contact.consent_evidence = evidence
    contact.consent_at = timezone.now()
    contact.consent_by = scope.user
    contact.save(
        update_fields=[
            "consent",
            "consent_evidence",
            "consent_at",
            "consent_by",
            "updated_at",
        ]
    )
    audit.record(scope, "contact.consent", contact, consent=consent)


def opt_out_number(
    organisation, number: str, *, reason="", source=OptOut.Source.STAFF, user=None
) -> OptOut:
    """Put a normalised number on the do-not-call list. Safe to repeat."""
    lookup = crypto.lookup(number)
    entry, _ = OptOut.objects.get_or_create(
        organisation=organisation,
        phone_lookup=lookup,
        defaults={
            "phone_encrypted": crypto.encrypt(number),
            "phone_masked": mask_phone(number),
            "reason": reason[:200],
            "source": source,
            "created_by": user,
        },
    )
    # The person has told us: their consent, if any, is withdrawn.
    Contact.objects.filter(
        organisation=organisation,
        phone_lookup=lookup,
        consent=Contact.Consent.GRANTED,
    ).update(consent=Contact.Consent.WITHDRAWN, consent_at=timezone.now())
    return entry


def opt_out(scope, contact: Contact, reason: str = "") -> OptOut:
    """Opt a stored contact out, without the caller ever seeing the number."""
    if contact.is_erased:
        raise CallNotPermittedError("This contact has been erased.")
    entry = opt_out_number(
        scope.organisation,
        crypto.decrypt(contact.phone_encrypted),
        reason=reason,
        user=scope.user,
    )
    audit.record(scope, "contact.opted_out", contact, reason=reason)
    return entry


def call_block(contact: Contact) -> str:
    """Why this contact must not be called, or "" if it may be."""
    if contact.is_erased:
        return "This contact has been erased."
    if is_opted_out(contact.organisation, contact.phone_lookup):
        return "This number is on the do-not-call list."
    if contact.consent != Contact.Consent.GRANTED:
        return (
            f"Permission to call is “{contact.get_consent_display().lower()}”. "
            "Record that the person agreed to be called first."
        )
    return ""


def number_for_call(contact: Contact, *, actor, purpose: str) -> str:
    """The full number, for placing a permitted call and for nothing else.

    Refuses unless the contact may be called, and unless dialling stored
    contacts has been switched on for this deployment. Every use is logged.
    """
    reason = call_block(contact)
    if reason:
        raise CallNotPermittedError(reason)
    if not settings.CONTACT_DIALING_ENABLED:
        raise CallNotPermittedError(
            "Calling stored contacts is switched off on this installation. "
            "Calls go only to the approved test number."
        )
    number = crypto.decrypt(contact.phone_encrypted)
    audit.record_system(
        contact.organisation,
        "contact.number_used",
        actor=actor,
        contact=str(contact.pk),
        purpose=purpose,
    )
    return number


def erase(contact: Contact) -> None:
    """Remove the personal details and keep an empty row, so reports and
    call history still add up. An opt-out for the number is kept."""
    with transaction.atomic():
        contact.name = "Erased contact"
        contact.phone_encrypted = ""
        contact.phone_masked = ""
        # Not a hash of anything: it only keeps the row unique.
        contact.phone_lookup = f"erased:{uuid.uuid4().hex}"
        contact.origin_detail = ""
        contact.consent_evidence = ""
        contact.best_time = ""
        contact.erased_at = timezone.now()
        contact.save()
        for lead in contact.leads.all():
            lead.notes.all().delete()
            lead.details = ""
            if lead.is_open:
                lead.stage = Lead.Stage.LOST
                lead.closed_at = timezone.now()
            lead.save()
            lead.follow_ups.filter(done_at__isnull=True).delete()
