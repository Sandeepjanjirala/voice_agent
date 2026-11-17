"""Celery tasks. Each is a thin wrapper over apps/operations/jobs.py, so the
same work runs from Beat, from a management command and from tests."""

from celery import shared_task
from django.core.cache import cache

from . import jobs


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


@shared_task(name="operations.deliver_webhook", ignore_result=True)
def deliver_webhook(delivery_id: int) -> None:
    from apps.integrations import webhooks

    webhooks.deliver(delivery_id)


@shared_task(name="operations.analyse_calls", ignore_result=True)
def analyse_calls() -> None:
    jobs.analyse_calls()


@shared_task(name="operations.deliver_webhooks", ignore_result=True)
def deliver_webhooks() -> None:
    jobs.deliver_webhooks()


@shared_task(name="operations.follow_up_reminders", ignore_result=True)
def follow_up_reminders() -> None:
    jobs.follow_up_reminders()


@shared_task(name="operations.campaign_eligibility", ignore_result=True)
def campaign_eligibility() -> None:
    jobs.campaign_eligibility()


@shared_task(name="operations.usage_report", ignore_result=True)
def usage_report() -> None:
    jobs.usage_report()


@shared_task(name="operations.retention", ignore_result=True)
def retention() -> None:
    jobs.retention()
