"""Celery tasks. Each is a thin wrapper over apps/operations/jobs.py, so the
same work runs from Beat, from a management command and from tests."""

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from . import jobs


@shared_task(name="operations.deployment_heartbeat", ignore_result=True)
def deployment_heartbeat() -> None:
    """Prove that Beat delivered a recent task and a worker consumed it.

    Messages expire after 60 seconds so a backlog cannot make a long-stopped
    scheduler appear healthy. No provider, database or calling side effects.
    """
    from .deployment import BEAT_HEARTBEAT_KEY, BEAT_MAX_AGE

    cache.set(BEAT_HEARTBEAT_KEY, timezone.now().timestamp(), timeout=BEAT_MAX_AGE)


@shared_task(name="operations.analyse_call", ignore_result=True)
def analyse_call(call_id: str) -> None:
    """Analyse one call as soon as its transcript arrives. A second task for
    the same call while one is running does nothing."""
    from apps.calling import analysis
    from apps.telephony.models import CallRecord

    if not cache.add(f"analyse-call:{call_id}", True, timeout=300):
        return
    try:
        record = CallRecord.objects.filter(pk=call_id).first()
        if record is not None and not record.is_active:
            analysis.analyse(record)
    finally:
        cache.delete(f"analyse-call:{call_id}")


@shared_task(name="operations.analyse_calls", ignore_result=True)
def analyse_calls() -> None:
    jobs.analyse_calls()


@shared_task(name="operations.follow_up_reminders", ignore_result=True)
def follow_up_reminders() -> None:
    jobs.follow_up_reminders()


@shared_task(name="operations.usage_report", ignore_result=True)
def usage_report() -> None:
    jobs.usage_report()


@shared_task(name="operations.retention", ignore_result=True)
def retention() -> None:
    jobs.retention()


@shared_task(name="operations.advance_call_lists", ignore_result=True)
def advance_call_lists() -> None:
    """Call lists keep calling, one after another, without a page open."""
    from apps.calling import calllists

    calllists.advance_running()
