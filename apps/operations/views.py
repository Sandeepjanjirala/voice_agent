"""Notifications, the operations page, usage and costs, and readiness."""

from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, connection
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from apps.tenancy.access import scoped

from .models import JobRun, Notification

JOB_NAMES = (
    "analyse_calls",
    "follow_up_reminders",
    "usage_report",
    "retention",
)


def visible_notifications(scope):
    """The organisation's notifications this person should see."""
    found = Notification.objects.filter(organisation=scope.organisation).filter(
        Q(user__isnull=True) | Q(user=scope.user)
    )
    if not scope.all_campuses:
        found = found.filter(Q(campus__isnull=True) | Q(campus__in=scope.campus_ids))
    return found


@scoped()
def notifications(request):
    scope = request.scope
    found = visible_notifications(scope)
    if request.method == "POST":
        found.filter(read_at__isnull=True).update(read_at=timezone.now())
        return redirect("notifications")
    return render(
        request,
        "operations/notifications.html",
        {"items": found[:100], "unread": found.filter(read_at__isnull=True).count()},
    )


def _broker() -> str:
    if not settings.REDIS_URL:
        return "Not configured: background jobs need REDIS_URL."
    try:
        import redis

        redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2).ping()
    except Exception as error:
        return f"Not reachable ({type(error).__name__})."
    return ""


@scoped("operations.view")
def operations(request):
    from apps.calling.models import CallResult

    scope = request.scope
    organisation = scope.organisation
    week = timezone.now() - timedelta(days=7)
    latest = {}
    for name in JOB_NAMES:
        latest[name] = JobRun.objects.filter(name=name).first()
    results = CallResult.objects.filter(call__organisation=organisation)
    return render(
        request,
        "operations/operations.html",
        {
            "jobs": [(name, latest[name]) for name in JOB_NAMES],
            "runs": JobRun.objects.all()[:30],
            "failed_jobs": JobRun.objects.filter(
                status=JobRun.Status.FAILED, started_at__gte=week
            ).count(),
            "analysis": {
                "waiting": results.filter(status=CallResult.Status.PENDING).count(),
                "retrying": results.filter(
                    status=CallResult.Status.FAILED, next_attempt_at__isnull=False
                ).count(),
                "gave_up": results.filter(
                    status=CallResult.Status.FAILED, next_attempt_at__isnull=True
                ).count(),
            },
            "broker_problem": _broker(),
            "eager": settings.CELERY_TASK_ALWAYS_EAGER,
            "schedule": [
                (name, entry["task"], int(entry["schedule"]))
                for name, entry in settings.CELERY_BEAT_SCHEDULE.items()
            ],
        },
    )


def _month(value: str) -> date:
    try:
        year, month = (int(part) for part in value.split("-"))
        return date(year, month, 1)
    except (ValueError, AttributeError):
        return timezone.localdate().replace(day=1)


@scoped("usage.view")
def usage(request):
    from apps.calling.models import CallResult
    from apps.telephony.models import CallRecord
    from apps.tenancy.models import AuditEvent

    scope = request.scope
    organisation = scope.organisation
    start = _month(request.GET.get("month", ""))
    end = (start + timedelta(days=32)).replace(day=1)
    calls = scope.limit(
        CallRecord.objects.filter(created_at__date__gte=start, created_at__date__lt=end)
    )
    totals = calls.aggregate(
        n=Count("id"),
        answered=Count("id", filter=Q(answered_at__isnull=False)),
        talk=Sum("duration_seconds"),
    )
    minutes = Decimal(totals["talk"] or 0) / 60
    analyses = CallResult.objects.filter(
        call__in=calls, status=CallResult.Status.DONE
    ).count()
    previews = AuditEvent.objects.filter(
        organisation=organisation,
        action="voice.preview",
        created_at__date__gte=start,
        created_at__date__lt=end,
    ).count()
    cost = (
        minutes * organisation.cost_per_minute
        + analyses * organisation.cost_per_analysis
    ).quantize(Decimal("0.01"))

    def grouped(field, label_field):
        rows = (
            calls.values(field, label_field)
            .annotate(
                n=Count("id"),
                answered=Count("id", filter=Q(answered_at__isnull=False)),
                talk=Sum("duration_seconds"),
            )
            .order_by("-n")
        )
        return [
            {
                "name": row[label_field] or "–",
                "calls": row["n"],
                "answered": row["answered"],
                "minutes": round((row["talk"] or 0) / 60, 1),
                "cost": (
                    Decimal(row["talk"] or 0) / 60 * organisation.cost_per_minute
                ).quantize(Decimal("0.01")),
            }
            for row in rows
        ]

    months = []
    cursor = timezone.localdate().replace(day=1)
    for _ in range(6):
        months.append(cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    return render(
        request,
        "operations/usage.html",
        {
            "start": start,
            "months": months,
            "totals": {
                "calls": totals["n"],
                "answered": totals["answered"],
                "minutes": round(float(minutes), 1),
                "analyses": analyses,
                "previews": previews,
                "cost": cost,
            },
            "by_employee": grouped(
                "employee_version__employee", "employee_version__employee__name"
            ),
            "by_campus": grouped("campus", "campus__name"),
            "rates": organisation,
            "has_rates": any(
                (
                    organisation.cost_per_minute,
                    organisation.cost_per_analysis,
                )
            ),
        },
    )


@never_cache
def readiness(request):
    """For a load balancer or monitor: can this instance serve requests and run
    jobs? Answers booleans only, never configuration values."""
    from apps.crm import crypto

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        database = True
    except DatabaseError:
        database = False
    try:
        cache.set("readiness-probe", 1, timeout=5)
        cache_ok = cache.get("readiness-probe") == 1
    except Exception:
        cache_ok = False
    checks = {
        "database": database,
        "cache": cache_ok,
        "contact_keys": crypto.configured(),
        "job_broker": not _broker(),
    }
    ready = all(checks.values())
    return JsonResponse({"ready": ready, **checks}, status=200 if ready else 503)
