"""Call lists: numbers typed in or uploaded (Excel or CSV), called one after
another by a published AI employee.

- **Reading the list.** One number per line, optionally with a name ("Asha,
  98765 43210"), or an .xlsx/.csv file with a phone (or mobile, number) column
  and an optional name column. Rows that are not Indian mobile numbers, or that
  repeat a number, are left out and listed with the reason.
- **Before calling.** The person starting the calls confirms that everyone on
  the list agreed to be called; it is recorded with the list.
- **Calling.** `advance()` takes one step: it checks the call in progress and,
  once the line is free, calls the next person. It is safe to run from the open
  list page (every few seconds) and from the scheduler at the same time: the
  database allows one active outbound call, so nobody is called twice at once.
  Each call goes through the same checks as any other: the dialing policy, the
  9 AM to 9 PM window (and the campus's hours), the do-not-call list and the
  monthly minutes budget.
"""

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.crm import contacts, crypto
from apps.crm.phones import InvalidPhoneError, normalize
from apps.telephony import calls
from apps.telephony.masking import mask_phone
from apps.telephony.models import CallRecord
from apps.tenancy import audit, hours

from .models import CallList, CallListEntry

logger = logging.getLogger(__name__)

Entry = CallListEntry.Status
MAX_ROWS = 500
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
PHONE_HEADERS = (
    "phone",
    "mobile",
    "number",
    "contact number",
    "phone number",
    "mobile number",
    "ph",
)
NAME_HEADERS = ("name", "parent", "parent name", "student", "student name", "guardian")
PHONE_TOKEN = re.compile(r"\+?\d[\d\s\-()]{8,}\d")
CONSENT_TEXT = "Everyone on this list agreed to be called by {organisation} about this."
# A step holds this lock, so two steps (the open page and the scheduler, or
# two open pages) never work on the same list at once.
STEP_LOCK_SECONDS = 120
# How long an entry may be "calling" with no call record yet: the other
# step may still be placing it.
DIAL_GRACE = timedelta(minutes=2)


class ListError(Exception):
    """A list that cannot be read or used, said plainly."""


@dataclass
class Parsed:
    rows: list[tuple[str, str]] = field(default_factory=list)  # (name, number)
    rejected: list[dict] = field(default_factory=list)  # {"row", "text", "reason"}
    duplicates: int = 0


# --- reading -------------------------------------------------------------------------------


def parse(text: str = "", upload=None) -> Parsed:
    """The people to call, from typed lines and/or an uploaded file."""
    lines: list[tuple[int, list[str]]] = []
    for index, line in enumerate((text or "").splitlines(), start=1):
        if line.strip():
            lines.append((index, _split_line(line)))
    if upload is not None:
        lines.extend(_read_upload(upload))
    parsed = Parsed()
    seen = set()
    for row, cells in lines:
        if len(parsed.rows) >= MAX_ROWS:
            parsed.rejected.append(
                {"row": row, "text": "", "reason": f"Only {MAX_ROWS} people per list"}
            )
            continue
        name, raw = _name_and_number(cells)
        if not raw:
            parsed.rejected.append(
                {"row": row, "text": _shown(cells), "reason": "No phone number found"}
            )
            continue
        try:
            number = normalize(raw)
        except InvalidPhoneError:
            parsed.rejected.append(
                {
                    "row": row,
                    "text": _shown(cells),
                    "reason": "Not a valid Indian mobile number",
                }
            )
            continue
        if number in seen:
            parsed.duplicates += 1
            continue
        seen.add(number)
        parsed.rows.append((name[:120], number))
    return parsed


def _shown(cells: list[str]) -> str:
    """What the row said, with any digits masked (it may hold a number)."""
    text = " ".join(" ".join(cells).split())[:60]
    return re.sub(r"\d(?=\d{2})", "*", text)


def _split_line(line: str) -> list[str]:
    match = PHONE_TOKEN.search(line)
    if not match:
        return [line.strip()]
    name = (line[: match.start()] + " " + line[match.end() :]).strip(" ,;:-\t")
    return [name, match.group()]


def _name_and_number(cells: list[str]) -> tuple[str, str]:
    number, name = "", ""
    for cell in cells:
        cell = " ".join(str(cell or "").split())
        if not cell:
            continue
        if not number and PHONE_TOKEN.fullmatch(cell.replace(".0", "")):
            number = cell.replace(".0", "")
        elif not name and not PHONE_TOKEN.search(cell):
            name = cell
    return name, number


def _read_upload(upload) -> list[tuple[int, list[str]]]:
    if upload.size > MAX_UPLOAD_BYTES:
        raise ListError("The file is too large (2 MB at most).")
    filename = (upload.name or "").lower()
    if filename.endswith(".xlsx"):
        rows = _xlsx_rows(upload)
    elif filename.endswith(".csv"):
        rows = _csv_rows(upload)
    else:
        raise ListError("Upload an Excel file (.xlsx) or a CSV file (.csv).")
    if not rows:
        return []
    header = [str(cell or "").strip().lower() for cell in rows[0]]
    phone_col = next((i for i, h in enumerate(header) if h in PHONE_HEADERS), None)
    if phone_col is None:
        return [(index, row) for index, row in enumerate(rows, start=1)]
    name_col = next((i for i, h in enumerate(header) if h in NAME_HEADERS), None)
    found = []
    for index, row in enumerate(rows[1:], start=2):
        phone = row[phone_col] if phone_col < len(row) else ""
        name = row[name_col] if name_col is not None and name_col < len(row) else ""
        if str(phone or "").strip() or str(name or "").strip():
            found.append((index, [str(name or ""), str(phone or "")]))
    return found


def _cell(value) -> str:
    # Excel stores 9876543210 as a number: 9876543210.0 must stay digits.
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "" if value is None else str(value)


def _xlsx_rows(upload) -> list[list[str]]:
    from openpyxl import load_workbook

    try:
        book = load_workbook(io.BytesIO(upload.read()), read_only=True, data_only=True)
    except Exception:
        raise ListError(
            "The Excel file could not be read. Save it as .xlsx and try again."
        ) from None
    sheet = book.worksheets[0]
    rows = _within_limit(
        [_cell(value) for value in row] for row in sheet.iter_rows(values_only=True)
    )
    book.close()
    return rows


def _csv_rows(upload) -> list[list[str]]:
    raw = upload.read()
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ListError(
            "The CSV file could not be read. Save it as UTF-8 and try again."
        )
    return _within_limit(csv.reader(io.StringIO(text)))


def _within_limit(rows) -> list[list[str]]:
    """The file's rows, refused whole when it holds more people than a list
    may: cutting it short would leave the rest out without a word. Empty rows
    (Excel keeps formatted blank ones) do not count."""
    kept, filled = [], 0
    for row in rows:
        if any(str(cell).strip() for cell in row):
            filled += 1
            # One more than the limit: the first row may be the column names.
            if filled > MAX_ROWS + 1:
                raise ListError(
                    f"This file has more than {MAX_ROWS} rows. A list holds up to "
                    f"{MAX_ROWS} people: split the file and make one list per part."
                )
        kept.append(row)
    return kept


# --- making a list ---------------------------------------------------------------------


def create(scope, *, name, purpose, employee, campus, parsed: Parsed) -> CallList:
    if not crypto.configured():
        raise ListError("Phone numbers cannot be stored: the contact keys are not set.")
    if not parsed.rows:
        raise ListError("No valid mobile numbers were found.")
    with transaction.atomic():
        call_list = CallList.objects.create(
            organisation=scope.organisation,
            campus=campus,
            employee=employee,
            name=name,
            purpose=purpose,
            created_by=scope.user,
            rejected_rows=parsed.rejected[:200],
        )
        entries = []
        for position, (person, number) in enumerate(parsed.rows, start=1):
            lookup = crypto.lookup(number)
            blocked = contacts.is_opted_out(scope.organisation, lookup)
            entries.append(
                CallListEntry(
                    call_list=call_list,
                    position=position,
                    name=person,
                    phone_encrypted=crypto.encrypt(number),
                    phone_lookup=lookup,
                    phone_masked=mask_phone(number),
                    status=Entry.SKIPPED if blocked else Entry.WAITING,
                    reason="On the do-not-call list" if blocked else "",
                )
            )
        CallListEntry.objects.bulk_create(entries)
    audit.record(scope, "call_list.created", call_list, people=len(entries))
    return call_list


def start(scope, call_list: CallList) -> None:
    call_list.status = CallList.Status.RUNNING
    call_list.consent_confirmed_at = timezone.now()
    call_list.consent_text = CONSENT_TEXT.format(
        organisation=call_list.organisation.name
    )
    call_list.waiting = ""
    call_list.save()
    audit.record(scope, "call_list.started", call_list)


# --- calling, one after another ----------------------------------------------------------


def advance(call_list: CallList, *, gateway=None, now=None) -> CallList:
    """One step: settle the call in progress, then call the next person when
    the line is free and the rules allow. A step already running for this list
    (another page or the scheduler) makes this one do nothing."""
    lock = f"call-list-step:{call_list.pk}"
    if not cache.add(lock, True, timeout=STEP_LOCK_SECONDS):
        return call_list
    try:
        return _advance(call_list, gateway, now or timezone.now())
    finally:
        cache.delete(lock)


def _advance(call_list: CallList, gateway, now) -> CallList:
    current = (
        call_list.entries.filter(status=Entry.CALLING).select_related("call").first()
    )
    if current is not None and not _settle(current, gateway, now):
        return call_list  # still on the line
    call_list.refresh_from_db()
    if call_list.status != CallList.Status.RUNNING:
        return call_list
    if not call_list.entries.filter(status=Entry.WAITING).exists():
        return _set(call_list, status=CallList.Status.DONE, waiting="")
    reason = _wait_reason(call_list, now, gateway)
    if reason:
        return _set(call_list, waiting=reason)
    with transaction.atomic():
        entry = (
            call_list.entries.select_for_update()
            .filter(status=Entry.WAITING)
            .order_by("position")
            .first()
        )
        if entry is None:
            return call_list
        entry.status, entry.reason = Entry.CALLING, ""
        entry.save(update_fields=["status", "reason", "updated_at"])
    return _dial(call_list, entry, gateway)


def _set(call_list: CallList, **fields) -> CallList:
    for name, value in fields.items():
        setattr(call_list, name, value)
    call_list.save(update_fields=[*fields, "updated_at"])
    return call_list


def _wait_reason(call_list: CallList, now, gateway=None) -> str:
    opens, closes = calls.LIVE_CALLING_HOURS
    local = timezone.localtime(now)
    if not opens <= local.hour < closes:
        return f"Calls are made between {opens} AM and {closes - 12} PM. Calling continues at {opens} AM."
    if call_list.campus is not None and not hours.is_open(call_list.campus, now):
        return (
            f"{call_list.campus.name} is closed now. Calling continues when it opens."
        )
    if _line_busy(gateway):
        return "Another call is on the line. The next call starts when it ends."
    organisation = call_list.organisation
    if organisation.monthly_call_minutes:
        start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        used = (
            CallRecord.objects.filter(
                organisation=organisation, created_at__gte=start
            ).aggregate(total=Sum("duration_seconds"))["total"]
            or 0
        ) / 60
        if used >= organisation.monthly_call_minutes:
            return f"This month's calling budget of {organisation.monthly_call_minutes} minutes is used up."
    return ""


def _line_busy(gateway) -> bool:
    """Whether an outbound call is really on the line. A record left active by a
    worker that stopped is closed once it is older than any call could last,
    and the others are checked with LiveKit, so a stale record cannot hold the
    list back for ever."""
    active = CallRecord.objects.filter(
        direction=CallRecord.Direction.OUTBOUND, status__in=CallRecord.ACTIVE
    )
    if not active.exists():
        return False
    calls.close_stale()
    for record in active:
        calls.refresh(record, gateway=gateway)
    return active.exists()


def _dial(call_list: CallList, entry: CallListEntry, gateway) -> CallList:
    from apps.workforce import publishing

    def give_back(waiting: str, *, pause: bool = False) -> CallList:
        entry.status = Entry.WAITING
        entry.save(update_fields=["status", "updated_at"])
        fields = {"waiting": waiting}
        if pause:
            fields["status"] = CallList.Status.PAUSED
        return _set(call_list, **fields)

    if contacts.is_opted_out(call_list.organisation, entry.phone_lookup):
        _finish(entry, Entry.SKIPPED, "On the do-not-call list")
        return call_list
    if call_list.employee is None:
        # Older lists may name no employee; every call needs one now.
        return give_back("Paused: choose an AI employee for these calls.", pause=True)
    try:
        plan = publishing.plan_call(
            call_list.employee,
            campus=call_list.campus,
            caller_name=entry.name,
            about=call_list.purpose,
        )
        record = calls.start_call(
            user=call_list.created_by,
            organisation=call_list.organisation,
            to=crypto.decrypt(entry.phone_encrypted),
            call_plan=plan,
            campus=call_list.campus,
            gateway=gateway,
        )
    except publishing.PublishError as error:
        return give_back(f"Paused: {error}", pause=True)
    except calls.CallBusyError:
        return give_back(
            "Another call is on the line. The next call starts when it ends."
        )
    except calls.CallNotReadyError as error:
        return give_back(f"Paused: {error}", pause=True)
    except calls.CallError as error:
        _finish(entry, Entry.FAILED, str(error))
        return call_list
    except crypto.CryptoError:
        _finish(entry, Entry.FAILED, "The stored number could not be read.")
        return call_list
    entry.call = record
    entry.save(update_fields=["call", "updated_at"])
    return _set(call_list, waiting="")


def _settle(entry: CallListEntry, gateway, now=None) -> bool:
    """Whether the entry's call is over (and the entry finished)."""
    record = entry.call
    if record is None:
        # Another step may still be placing it; give it time before giving up.
        if entry.updated_at > (now or timezone.now()) - DIAL_GRACE:
            return False
        _finish(entry, Entry.FAILED, "The call was not placed.")
        return True
    if record.is_active:
        record = calls.refresh(record, gateway=gateway)
    if record.is_active:
        return False
    done = CallRecord.Status
    if record.status == done.COMPLETED or record.answered_at is not None:
        talk = (
            f"{round(record.duration_seconds)} s talk"
            if record.duration_seconds
            else ""
        )
        _finish(entry, Entry.ANSWERED, talk)
    elif record.status in (done.NO_ANSWER, done.REJECTED):
        _finish(entry, Entry.NO_ANSWER, record.get_status_display())
    else:
        _finish(
            entry, Entry.FAILED, record.failure_reason or record.get_status_display()
        )
    return True


def _finish(entry: CallListEntry, status: str, reason: str) -> None:
    entry.status, entry.reason = status, reason[:200]
    entry.save(update_fields=["status", "reason", "updated_at"])


def counts(call_list: CallList) -> dict[str, int]:
    found = dict.fromkeys(Entry.values, 0)
    for status in call_list.entries.values_list("status", flat=True):
        found[status] += 1
    found["total"] = sum(found[value] for value in Entry.values)
    found["finished"] = found["total"] - found[Entry.WAITING] - found[Entry.CALLING]
    return found


def needs_steps(call_list: CallList) -> bool:
    """A running list, or a paused or cancelled one whose last call is still
    on the line and must be settled."""
    return (
        call_list.status == CallList.Status.RUNNING
        or call_list.entries.filter(status=Entry.CALLING).exists()
    )


def advance_running(gateway=None) -> int:
    """For the scheduler: one step for every list that is calling, or still
    has a call on the line after being paused or cancelled."""
    stepped = 0
    lists = CallList.objects.filter(
        Q(status=CallList.Status.RUNNING) | Q(entries__status=Entry.CALLING)
    ).distinct()
    for call_list in lists:
        try:
            advance(call_list, gateway=gateway)
            stepped += 1
        except Exception:
            logger.exception(
                "call list step failed", extra={"call_list": str(call_list.pk)}
            )
    return stepped
