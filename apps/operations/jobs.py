"""The scheduled jobs. Each one is safe to run twice or late.

`tracked()` records every run as a JobRun and holds a cache lock so two runs
of one job never overlap. The work itself is idempotent: each item carries the
state that says whether it has been done.
"""

import logging
from contextlib import contextmanager
from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import JobRun, Notification, UsageDay

logger = logging.getLogger(__name__)
LOCK_SECONDS = 600


@contextmanager
def tracked(name: str):
    """Run a job under a lock, recording the run. Yields the summary dict,
    or None when the job is already running elsewhere."""
    lock = f"job-lock:{name}"
    run = JobRun.objects.create(name=name)
    if not cache.add(lock, run.pk, timeout=LOCK_SECONDS):
        run.status, run.finished_at = JobRun.Status.SKIPPED, timezone.now()
        run.save()
        yield None
        return
    summary: dict = {}
    try:
        yield summary
    except Exception as error:
        run.status, run.error = (
            JobRun.Status.FAILED,
            f"{type(error).__name__}: {error}"[:300],
        )
        logger.exception("background job failed", extra={"job": name})
        raise
    else:
        run.status = JobRun.Status.OK
    finally:
        run.summary, run.finished_at = summary, timezone.now()
        run.save()
        cache.delete(lock)


def notify(organisation, kind, title, *, url="", key="", campus=None, user=None):
    """Add a notification once per key. Returns it, or None if already there."""
    try:
        # A savepoint: a duplicate must not spoil the caller's transaction.
        with transaction.atomic():
            return Notification.objects.create(
                organisation=organisation,
                campus=campus,
                user=user,
                kind=kind,
                title=title[:200],
                url=url[:300],
                key=key[:80],
            )
    except IntegrityError:
        return None


# --- the jobs ---------------------------------------------------------------------


def analyse_calls(limit: int = 50) -> dict:
    from apps.calling import analysis

    with tracked("analyse_calls") as summary:
        if summary is None:
            return {}
        counts: dict[str, int] = {}
        for result in list(analysis.pending()[:limit]):
            done = analysis.analyse(result.call)
            counts[done.status] = counts.get(done.status, 0) + 1
        summary.update(counts)
        return summary


def follow_up_reminders(now=None) -> dict:
    """A notification for each follow-up that has come due, once."""
    from apps.crm.models import FollowUp

    now = now or timezone.now()
    with tracked("follow_up_reminders") as summary:
        if summary is None:
            return {}
        due = FollowUp.objects.filter(
            done_at__isnull=True, due_at__lte=now
        ).select_related("lead", "organisation", "campus")
        sent = 0
        for item in due:
            created = notify(
                item.organisation,
                Notification.Kind.FOLLOW_UP,
                f"Follow-up due: {item.title} ({item.lead.subject})",
                url=f"/leads/{item.lead_id}/",
                key=f"follow-up:{item.pk}",
                campus=item.campus,
                user=item.assigned_to,
            )
            sent += created is not None
        summary["notified"] = sent
        return summary


def usage_report(days: int = 2) -> dict:
    """Write today's and yesterday's usage, by campus and employee."""
    from apps.calling.models import CallAction, CallResult
    from apps.telephony.models import CallRecord

    with tracked("usage_report") as summary:
        if summary is None:
            return {}
        today = timezone.localdate()
        rows = 0
        for offset in range(days):
            day = today - timedelta(days=offset)
            calls = CallRecord.objects.filter(
                created_at__date=day, organisation__isnull=False
            )
            groups = calls.values(
                "organisation", "campus", "employee_version__employee"
            ).annotate(
                n=Count("id"),
                answered=Count("id", filter=Q(answered_at__isnull=False)),
                talk=Sum("duration_seconds"),
            )
            for group in groups:
                same = Q(
                    call__organisation=group["organisation"],
                    call__campus=group["campus"],
                    call__employee_version__employee=group[
                        "employee_version__employee"
                    ],
                    call__created_at__date=day,
                )
                UsageDay.objects.update_or_create(
                    organisation_id=group["organisation"],
                    day=day,
                    campus_id=group["campus"],
                    employee_id=group["employee_version__employee"],
                    defaults={
                        "calls": group["n"],
                        "answered": group["answered"],
                        "talk_seconds": int(group["talk"] or 0),
                        "analyses": CallResult.objects.filter(
                            same, status=CallResult.Status.DONE
                        ).count(),
                        "actions": CallAction.objects.filter(same).count(),
                    },
                )
                rows += 1
        summary["rows"] = rows
        return summary


def retention() -> dict:
    from apps.crm import retention as policy
    from apps.tenancy.models import Organisation

    with tracked("retention") as summary:
        if summary is None:
            return {}
        totals: dict[str, int] = {}
        for organisation in Organisation.objects.all():
            for kind, count in policy.apply(organisation).items():
                totals[kind] = totals.get(kind, 0) + count
        summary.update(totals)
        return summary
