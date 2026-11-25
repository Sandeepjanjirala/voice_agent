"""Apply an organisation's retention policy: erase old contacts, delete old
call records, kept transcripts, call recordings and access-log entries. Run from the settings page or with
`manage.py retention`; both show what would happen before doing it."""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.calling import recordings
from apps.calling.models import CallRecording, CallTranscript
from apps.telephony.models import CallRecord
from apps.tenancy import audit
from apps.tenancy.models import AuditEvent, RetentionPolicy

from . import contacts
from .models import Contact, Lead


def policy_for(organisation) -> RetentionPolicy:
    policy, _ = RetentionPolicy.objects.get_or_create(organisation=organisation)
    return policy


def _due(organisation, now=None) -> dict:
    """Querysets of what the policy says should go, keyed by kind."""
    now = now or timezone.now()
    policy = policy_for(organisation)
    due = {
        "contacts": Contact.objects.none(),
        "calls": CallRecord.objects.none(),
        "transcripts": CallTranscript.objects.none(),
        "recordings": CallRecording.objects.none(),
        "audit": AuditEvent.objects.none(),
    }
    if policy.contact_days:
        cutoff = now - timedelta(days=policy.contact_days)
        due["contacts"] = (
            Contact.objects.filter(
                organisation=organisation, erased_at__isnull=True, updated_at__lt=cutoff
            )
            .filter(Q(last_contacted_at__isnull=True) | Q(last_contacted_at__lt=cutoff))
            .exclude(leads__stage__in=Lead.OPEN_STAGES)
        )
    if policy.call_days:
        due["calls"] = CallRecord.objects.filter(
            organisation=organisation,
            created_at__lt=now - timedelta(days=policy.call_days),
        ).exclude(status__in=CallRecord.ACTIVE)
    if policy.transcript_days:
        due["transcripts"] = CallTranscript.objects.filter(
            call__organisation=organisation,
            created_at__lt=now - timedelta(days=policy.transcript_days),
        )
    if policy.recording_days:
        # Kept separately from transcripts: a recording can be kept for less
        # (or more) time than the words.
        due["recordings"] = CallRecording.objects.filter(
            organisation=organisation,
            created_at__lt=now - timedelta(days=policy.recording_days),
        ).exclude(status=CallRecording.Status.DELETED)
    if policy.audit_days:
        due["audit"] = AuditEvent.objects.filter(
            organisation=organisation,
            created_at__lt=now - timedelta(days=policy.audit_days),
        )
    return due


def preview(organisation, now=None) -> dict[str, int]:
    return {kind: found.count() for kind, found in _due(organisation, now).items()}


def apply(organisation, actor=None, now=None) -> dict[str, int]:
    """Carry out the policy. Returns how many of each kind were removed."""
    due = _due(organisation, now)
    counts = {"contacts": 0}
    for contact in list(due["contacts"]):
        contacts.erase(contact)
        counts["contacts"] += 1
    counts["transcripts"] = due["transcripts"].count()
    due["transcripts"].delete()
    counts["recordings"] = 0
    for recording in list(due["recordings"]):
        try:
            recordings.delete(
                recording, "Deleted under the organisation's retention policy."
            )
            counts["recordings"] += 1
        except recordings.RecordingError:
            # Left as it is, and tried again next time.
            continue
    counts["calls"] = due["calls"].count()
    due["calls"].delete()
    counts["audit"] = due["audit"].count()
    due["audit"].delete()
    audit.record_system(organisation, "retention.applied", actor=actor, **counts)
    return counts
