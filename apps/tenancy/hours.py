"""When a campus is open: weekly hours, minus holidays.

A campus with no opening hours set is treated as always open, so hours only
restrict anything once an admin has entered them.
"""

from datetime import date, datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from .models import Holiday, OpeningHours

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
LOOKAHEAD_DAYS = 21


def holiday_on(campus, day: date) -> Holiday | None:
    if campus is None:
        return None
    return (
        Holiday.objects.filter(organisation_id=campus.organisation_id, date=day)
        .filter(Q(campus=campus) | Q(campus__isnull=True))
        .first()
    )


def hours_on(campus, day: date) -> tuple | None:
    """(opens, closes) for that day, or None when closed. None also when
    the campus has no hours at all; check `has_hours` to tell them apart."""
    if holiday_on(campus, day) is not None:
        return None
    row = OpeningHours.objects.filter(campus=campus, weekday=day.weekday()).first()
    return (row.opens, row.closes) if row else None


def has_hours(campus) -> bool:
    return campus is not None and OpeningHours.objects.filter(campus=campus).exists()


def is_open(campus, at: datetime | None = None) -> bool:
    if not has_hours(campus):
        return holiday_on(campus, timezone.localdate(at)) is None if campus else True
    local = timezone.localtime(at or timezone.now())
    window = hours_on(campus, local.date())
    return window is not None and window[0] <= local.time() < window[1]


def next_open(campus, at: datetime | None = None) -> datetime | None:
    """The next moment the campus opens (now, if it is open)."""
    local = timezone.localtime(at or timezone.now())
    if is_open(campus, local):
        return local
    if not has_hours(campus):
        for offset in range(1, LOOKAHEAD_DAYS):
            day = local.date() + timedelta(days=offset)
            if holiday_on(campus, day) is None:
                return timezone.make_aware(datetime.combine(day, datetime.min.time()))
        return None
    for offset in range(LOOKAHEAD_DAYS):
        day = local.date() + timedelta(days=offset)
        window = hours_on(campus, day)
        if window is None:
            continue
        opening = timezone.make_aware(datetime.combine(day, window[0]))
        if opening > local:
            return opening
    return None


def describe(campus) -> str:
    """Opening hours in one line, e.g. "Mon–Fri 09:00–16:00, Sat 09:00–13:00"."""
    rows = list(OpeningHours.objects.filter(campus=campus).order_by("weekday"))
    if not rows:
        return ""
    groups: list[list] = []
    for row in rows:
        span = (row.opens, row.closes)
        if groups and groups[-1][1] == span and groups[-1][0][-1] == row.weekday - 1:
            groups[-1][0].append(row.weekday)
        else:
            groups.append([[row.weekday], span])
    parts = []
    for days, (opens, closes) in groups:
        label = (
            WEEKDAYS[days[0]]
            if len(days) == 1
            else f"{WEEKDAYS[days[0]]}–{WEEKDAYS[days[-1]]}"
        )
        parts.append(f"{label} {opens:%H:%M}–{closes:%H:%M}")
    return ", ".join(parts)
