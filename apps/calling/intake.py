"""Instant leads: a website form or another system posts a new enquiry.

Each source has its own address and secret. The secret is compared by its
SHA-256, so the database never holds it. A delivery is checked, matched to an
existing contact by the keyed hash, refused if the number is on the
do-not-call list, and turned into a lead queued for a call. With
`"dry_run": true` everything is checked and nothing is stored, as a test.
"""

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.crm import contacts, crypto
from apps.crm.models import Contact, Lead
from apps.crm.phones import InvalidPhoneError, normalize
from apps.telephony.masking import mask_phone

from .models import IntakeEvent, LeadSource, QueuedCall

SECRET_HEADER = "HTTP_X_LEAD_SECRET"
DUPLICATE_WINDOW = timedelta(hours=24)
Result = IntakeEvent.Result


class IntakeRefusedError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def new_secret() -> tuple[str, str, str]:
    """(secret to show once, its hash to store, a hint to recognise it)."""
    secret = "ls_" + secrets.token_urlsafe(32)
    return secret, hash_secret(secret), secret[-4:]


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def authenticate(source: LeadSource | None, secret: str) -> LeadSource:
    """The source, if it is active and the secret matches. Same answer for an
    unknown source and a wrong secret, so neither can be probed."""
    if (
        source is None
        or not source.is_active
        or not secret
        or not hmac.compare_digest(source.secret_hash, hash_secret(secret))
    ):
        raise IntakeRefusedError(401, "Unknown source or wrong secret.")
    return source


def _text(data: dict, name: str, limit: int) -> str:
    value = data.get(name)
    return " ".join(str(value).split())[:limit] if isinstance(value, str) else ""


def receive(source: LeadSource, data: dict) -> tuple[IntakeEvent, Lead | None]:
    """Check one delivery and, unless it is a test, store it."""
    if not isinstance(data, dict):
        raise IntakeRefusedError(400, "Send a JSON object.")
    test = data.get("dry_run") is True
    try:
        number = normalize(data.get("phone"))
    except InvalidPhoneError as error:
        IntakeEvent.objects.create(
            source=source, result=Result.INVALID, detail=str(error)[:200]
        )
        raise IntakeRefusedError(400, str(error)) from None
    masked = mask_phone(number)
    name = _text(data, "name", 120) or "Website enquiry"
    subject = _text(data, "subject", 160) or f"Enquiry via {source.name}"[:160]
    details = _text(data, "details", 2000)
    if not crypto.configured():
        raise IntakeRefusedError(503, "Contacts cannot be stored on this installation.")
    lookup = crypto.lookup(number)
    if contacts.is_opted_out(source.organisation, lookup):
        event = IntakeEvent.objects.create(
            source=source,
            result=Result.TEST if test else Result.OPTED_OUT,
            detail="The number is on the do-not-call list; no call will be made.",
            masked_number=masked,
        )
        return event, None
    existing = contacts.find(source.organisation, number)
    if existing is not None and existing.is_erased:
        existing = None
    if existing is not None:
        recent = Lead.objects.filter(
            contact=existing,
            created_at__gte=timezone.now() - DUPLICATE_WINDOW,
            stage__in=Lead.OPEN_STAGES,
        ).first()
        if recent is not None:
            event = IntakeEvent.objects.create(
                source=source,
                result=Result.TEST if test else Result.DUPLICATE,
                detail="This person already has an open enquiry from the last day.",
                masked_number=masked,
                lead=None if test else recent,
            )
            return event, None
    if test:
        event = IntakeEvent.objects.create(
            source=source,
            result=Result.TEST,
            detail="Valid. It would be stored and queued for a call.",
            masked_number=masked,
        )
        return event, None

    evidence = (
        f"Submitted through “{source.name}” on "
        f"{timezone.localtime():%d %b %Y %H:%M}. The form says: "
        f"{source.consent_statement}"
    )[:1000]
    with transaction.atomic():
        contact = existing
        if contact is None:
            scope = _SourceScope(source)
            try:
                contact = contacts.create(
                    scope,
                    source.campus,
                    name=name,
                    phone=number,
                    origin=Contact.Origin.ENQUIRY,
                    origin_detail=f"Lead source: {source.name}"[:200],
                    calling_purpose=subject,
                    consent=Contact.Consent.GRANTED,
                    consent_evidence=evidence,
                )
            except contacts.DuplicateContactError as error:
                contact = error.contact
        elif contact.consent == Contact.Consent.UNKNOWN:
            contact.consent = Contact.Consent.GRANTED
            contact.consent_evidence = evidence
            contact.consent_at = timezone.now()
            contact.save(
                update_fields=[
                    "consent",
                    "consent_evidence",
                    "consent_at",
                    "updated_at",
                ]
            )
        lead = Lead.objects.create(
            organisation=source.organisation,
            campus=contact.campus,
            contact=contact,
            subject=subject,
            details=details,
            employee=source.employee,
        )
        QueuedCall.objects.create(
            organisation=source.organisation,
            campus=contact.campus,
            contact=contact,
            lead=lead,
            source=source,
            due_at=timezone.now() + timedelta(minutes=source.call_delay_minutes),
        )
        event = IntakeEvent.objects.create(
            source=source,
            result=Result.ACCEPTED,
            detail="Stored and queued for a call.",
            masked_number=masked,
            lead=lead,
        )
    return event, lead


class _SourceScope:
    """Stands in for a signed-in person when a source creates a contact, so
    the access log says where it came from."""

    def __init__(self, source: LeadSource) -> None:
        self.organisation = source.organisation
        self.user = _SourceUser(source)


class _SourceUser:
    is_authenticated = False
    pk = None

    def __init__(self, source: LeadSource) -> None:
        self._name = f"lead source: {source.name}"[:150]

    def get_username(self) -> str:
        return self._name
