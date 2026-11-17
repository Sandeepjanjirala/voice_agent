"""Working through the call queue: who would be called, when, and who not.

Only dry runs exist. A dry run checks every waiting entry against the same
rules a real run must follow (permission to call, do-not-call, erased contacts,
recent calls, attempts left, calling days and hours, the daily limit and the
employee being ready) and records the decision. It dials nobody and uses no
attempt. Real dialling of stored contacts is switched off on this installation
(CONTACT_DIALING_ENABLED), so nothing here can place a call.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from apps.crm import contacts
from apps.crm.models import Contact
from apps.telephony.masking import mask_phone
from apps.tenancy import hours

from .models import Campaign, DryRun, LeadSource, QueuedCall

# The widest calling hours any campaign may use, whatever is configured.
EARLIEST = time(9, 0)
LATEST = time(21, 0)
PLANNED_SHOWN = 50
Status = QueuedCall.Status


@dataclass(frozen=True)
class Rules:
    window_start: time
    window_end: time
    days: tuple[int, ...]
    max_attempts: int
    retry_after: timedelta
    daily_limit: int
    cooldown: timedelta
    # Holidays of this campus (and the organisation's) are skipped.
    campus: object = None

    @classmethod
    def for_campaign(cls, campaign: Campaign) -> "Rules":
        return cls(
            window_start=max(_time(campaign.window_start), EARLIEST),
            window_end=min(_time(campaign.window_end), LATEST),
            days=tuple(campaign.days) or (0, 1, 2, 3, 4, 5),
            max_attempts=max(campaign.max_attempts, 1),
            retry_after=timedelta(minutes=campaign.retry_after_minutes),
            daily_limit=max(campaign.daily_limit, 1),
            cooldown=timedelta(days=campaign.cooldown_days),
            campus=campaign.campus,
        )

    @classmethod
    def for_source(cls, source: LeadSource) -> "Rules":
        return cls(
            window_start=max(_time(source.window_start), EARLIEST),
            window_end=min(_time(source.window_end), LATEST),
            days=(0, 1, 2, 3, 4, 5, 6),
            max_attempts=3,
            retry_after=timedelta(minutes=30),
            daily_limit=1000,
            cooldown=timedelta(0),
            campus=source.campus,
        )


def _time(value) -> time:
    if isinstance(value, str):
        return datetime.strptime(value, "%H:%M").time()
    return value


def next_slot(rules: Rules, at: datetime) -> datetime:
    """The first moment at or after `at` inside the calling days and hours."""
    local = timezone.localtime(at)
    for day in range(8):
        date = (local + timedelta(days=day)).date()
        if date.weekday() not in rules.days:
            continue
        if rules.campus is not None and hours.holiday_on(rules.campus, date):
            continue
        start = timezone.make_aware(datetime.combine(date, rules.window_start))
        end = timezone.make_aware(datetime.combine(date, rules.window_end))
        if day == 0 and local >= end:
            continue
        return max(start, local) if day == 0 else start
    return local


def retry_plan(rules: Rules, first: datetime) -> list[datetime]:
    """When each attempt would be made if nobody answered the one before."""
    attempts, at = [], first
    for _ in range(rules.max_attempts):
        at = next_slot(rules, at)
        attempts.append(at)
        at = at + rules.retry_after
    return attempts


def employee_problem(employee) -> str:
    if employee is None:
        return "No AI employee is chosen."
    if employee.live_version is None:
        return f"{employee.name} has not been published."
    if not employee.taking_calls:
        return f"{employee.name} is paused."
    return ""


def _skip_reason(entry: QueuedCall, rules: Rules, now: datetime) -> str:
    contact = entry.contact
    if contact.is_erased:
        return "The contact was erased."
    reason = contacts.call_block(contact)
    if reason:
        return reason
    if entry.attempts >= rules.max_attempts:
        return "No attempts left."
    if rules.cooldown:
        recent = QueuedCall.objects.filter(
            contact=contact,
            status__in=(Status.WOULD_CALL, Status.DONE),
            checked_at__gte=now - rules.cooldown,
        ).exclude(campaign=entry.campaign, source=entry.source)
        if recent.exists():
            return "Already called by another campaign recently."
    return ""


def dry_run(entries, rules: Rules, employee, *, user=None, campaign=None, source=None):
    """Decide every waiting entry and keep the report. Dials nobody."""
    now = timezone.now()
    organisation = (campaign or source).organisation
    blocked = employee_problem(employee)
    counts = {"would_call": 0, "skipped": 0}
    reasons: dict[str, int] = {}
    planned = []
    per_day: dict = {}
    for entry in entries.select_related("contact").order_by("created_at", "id"):
        if entry.status == Status.DONE:
            continue
        reason = blocked or _skip_reason(entry, rules, now)
        if reason:
            entry.status, entry.reason, entry.due_at = Status.SKIPPED, reason, None
            counts["skipped"] += 1
            reasons[reason] = reasons.get(reason, 0) + 1
        else:
            at = next_slot(rules, max(entry.due_at or now, now))
            # Respect the daily limit by moving later entries to the next day.
            while per_day.get(at.date(), 0) >= rules.daily_limit:
                at = next_slot(
                    rules,
                    timezone.make_aware(
                        datetime.combine(at.date() + timedelta(days=1), time(0, 0))
                    ),
                )
            per_day[at.date()] = per_day.get(at.date(), 0) + 1
            entry.status, entry.reason, entry.due_at = Status.WOULD_CALL, "", at
            counts["would_call"] += 1
            if len(planned) < PLANNED_SHOWN:
                planned.append(
                    {
                        "name": entry.contact.name,
                        "number": entry.contact.phone_masked,
                        "attempts": [
                            timezone.localtime(when).strftime("%d %b %H:%M")
                            for when in retry_plan(rules, at)
                        ],
                    }
                )
        entry.checked_at = now
        entry.save(update_fields=["status", "reason", "due_at", "checked_at"])
    summary = {
        **counts,
        "reasons": reasons,
        "planned": planned,
        "employee": employee.name if employee else "",
        "version": employee.live_version.number
        if employee is not None and employee.live_version
        else None,
        "blocked": blocked,
        "dialling_enabled": settings.CONTACT_DIALING_ENABLED,
    }
    return DryRun.objects.create(
        organisation=organisation,
        campaign=campaign,
        source=source,
        run_by=user,
        summary=summary,
    )


def add_contacts(campaign: Campaign, found) -> tuple[int, int]:
    """Queue contacts for a campaign. Returns (added, already queued)."""
    added = existing = 0
    for contact in found.filter(campus=campaign.campus, erased_at__isnull=True):
        _, created = QueuedCall.objects.get_or_create(
            campaign=campaign,
            contact=contact,
            defaults={
                "organisation": campaign.organisation,
                "campus": campaign.campus,
            },
        )
        if created:
            added += 1
        else:
            existing += 1
    return added, existing


def eligible_contacts(campus):
    """Contacts of a campus that have recorded permission to call."""
    return Contact.objects.filter(
        campus=campus, erased_at__isnull=True, consent=Contact.Consent.GRANTED
    )


def masked(contact: Contact) -> str:
    return contact.phone_masked or mask_phone("")
