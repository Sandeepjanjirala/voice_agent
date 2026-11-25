"""Numbers for the Performance page, worked out from rows the viewer may see."""

import statistics
from collections import Counter
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from apps.telephony.models import CallRecord

from .models import Lead

RANGES = (7, 30, 90)


def call_summary(calls) -> dict:
    """Headline figures for a queryset of call records."""
    rows = list(
        calls.values_list(
            "status", "answered_at", "duration_seconds", "e2e_latency_p50"
        )
    )
    total = len(rows)
    answered = [row for row in rows if row[1] is not None]
    talk = [row[2] for row in rows if row[2] is not None]
    latency = [row[3] for row in rows if row[3] is not None]
    return {
        "follow_up": calls.filter(result__follow_up_needed=True).count(),
        "with_issues": calls.filter(result__has_issues=True).count(),
        "total": total,
        "answered": len(answered),
        "answer_rate": round(100 * len(answered) / total) if total else None,
        "talk_minutes": round(sum(talk) / 60, 1),
        "average_talk": round(sum(talk) / len(talk)) if talk else None,
        "response": round(statistics.median(latency), 1) if latency else None,
    }


def _bars(pairs: list[tuple[str, int]]) -> list[dict]:
    """Label, count and a width (percent of the largest) for a bar chart."""
    largest = max((count for _, count in pairs), default=0)
    return [
        {
            "label": label,
            "count": count,
            "width": round(100 * count / largest) if largest else 0,
        }
        for label, count in pairs
    ]


def calls_by_day(calls, days: int) -> list[dict]:
    today = timezone.localdate()
    counts = Counter(
        timezone.localtime(created).date()
        for created in calls.values_list("created_at", flat=True)
    )
    dates = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    return _bars([(f"{day:%d %b}", counts.get(day, 0)) for day in dates])


def calls_by_status(calls) -> list[dict]:
    labels = dict(CallRecord.Status.choices)
    found = calls.values("status").annotate(n=Count("id")).order_by("-n")
    return _bars([(labels[row["status"]], row["n"]) for row in found])


def leads_by_stage(leads) -> list[dict]:
    counts = dict(leads.values_list("stage").annotate(n=Count("id")))
    return _bars([(label, counts.get(value, 0)) for value, label in Lead.Stage.choices])


def by_campus(campuses, calls, leads) -> list[dict]:
    """One row per campus given (those the viewer may see, or the one chosen)."""
    rows = []
    for campus in campuses:
        campus_leads = leads.filter(campus=campus)
        rows.append(
            {
                "name": campus.name,
                "leads": campus_leads.count(),
                "open": campus_leads.filter(stage__in=Lead.OPEN_STAGES).count(),
                "won": campus_leads.filter(stage=Lead.Stage.WON).count(),
                **call_summary(calls.filter(campus=campus)),
            }
        )
    return rows


def by_employee(employees, calls, leads) -> list[dict]:
    """One row per AI employee: its leads, and calls placed with its versions."""
    lead_counts = {
        row["employee"]: row
        for row in leads.exclude(employee=None)
        .values("employee")
        .annotate(n=Count("id"), won=Count("id", filter=Q(stage=Lead.Stage.WON)))
    }
    rows = []
    for employee in employees:
        counts = lead_counts.get(employee.pk, {"n": 0, "won": 0})
        rows.append(
            {
                "name": employee.name,
                "role": employee.role,
                "status": employee.status_label,
                "leads": counts["n"],
                "won": counts["won"],
                **call_summary(calls.filter(employee_version__employee=employee)),
            }
        )
    return rows
