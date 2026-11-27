"""Import a list of contacts: check every row first, then store the good ones.

The preview and the import run the same analysis, so what was shown is what
happens. Authorised users see the full number when checking the preview.
"""

import csv
import io
from dataclasses import dataclass

from django.db import transaction

from apps.telephony.masking import mask_phone
from apps.tenancy import audit

from . import contacts, crypto
from .models import Contact, ContactImport, Lead
from .phones import InvalidPhoneError, normalize

MAX_ROWS = 2000
MAX_BYTES = 512_000
NAME_COLUMNS = (
    "name",
    "full name",
    "contact",
    "contact name",
    "customer",
    "customer name",
    "parent",
    "guardian",
)
PHONE_COLUMNS = ("phone", "mobile", "number", "phone number", "mobile number")
SUBJECT_COLUMNS = ("subject", "enquiry", "interest", "about")

NEW = "new"
STATUS_LABELS = {
    NEW: "Will be added",
    "invalid": "Invalid",
    "repeated": "Repeated in this list",
    "existing": "Already a contact",
    "opted_out": "On the do-not-call list",
}


class ImportFormatError(ValueError):
    pass


@dataclass
class Row:
    line: int
    name: str
    masked: str
    subject: str
    status: str
    reason: str = ""
    # Present only for rows that will be stored; never rendered.
    number: str = ""
    display_phone: str = ""

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]


def _column(header: list[str], names: tuple[str, ...]) -> int | None:
    for index, title in enumerate(header):
        if title.strip().lower() in names:
            return index
    return None


def analyse(organisation, text: str) -> list[Row]:
    """Every row of a pasted or uploaded CSV, with what would happen to it."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ImportFormatError("The list is too large. Import it in smaller parts.")
    lines = [line for line in csv.reader(io.StringIO(text.strip())) if any(line)]
    if len(lines) < 2:
        raise ImportFormatError(
            "Add a header line with name and phone, then one contact per line."
        )
    header, body = lines[0], lines[1:]
    name_at, phone_at = _column(header, NAME_COLUMNS), _column(header, PHONE_COLUMNS)
    subject_at = _column(header, SUBJECT_COLUMNS)
    if name_at is None or phone_at is None:
        raise ImportFormatError(
            'The first line must name the columns, including "name" and "phone".'
        )
    if len(body) > MAX_ROWS:
        raise ImportFormatError(f"At most {MAX_ROWS} contacts per import.")

    def cell(cells: list[str], index: int | None) -> str:
        if index is None or index >= len(cells):
            return ""
        return " ".join(cells[index].split())

    rows, seen = [], set()
    for line, cells in enumerate(body, start=2):
        name = cell(cells, name_at)[:120]
        subject = cell(cells, subject_at)[:160]
        raw = cell(cells, phone_at)
        try:
            number = normalize(raw)
        except InvalidPhoneError as error:
            rows.append(Row(line, name, "", subject, "invalid", str(error)))
            continue
        row = Row(line, name, mask_phone(number), subject, NEW, display_phone=number)
        lookup = crypto.lookup(number)
        if not name:
            row.status, row.reason = "invalid", "The name is empty."
        elif lookup in seen:
            row.status = "repeated"
        elif contacts.is_opted_out(organisation, lookup):
            row.status = "opted_out"
        elif Contact.objects.filter(
            organisation=organisation, phone_lookup=lookup
        ).exists():
            row.status = "existing"
        else:
            row.number = number
        seen.add(lookup)
        rows.append(row)
    return rows


def summary(rows: list[Row]) -> dict[str, int]:
    counts = dict.fromkeys(STATUS_LABELS, 0)
    for row in rows:
        counts[row.status] += 1
    return counts


def run(
    scope,
    campus,
    text: str,
    *,
    origin: str,
    calling_purpose: str,
    consent_attested: bool,
    consent_evidence: str,
    create_leads: bool,
    default_subject: str,
) -> ContactImport:
    """Store every row that analyse() marks as new."""
    rows = analyse(scope.organisation, text)
    counts = summary(rows)
    consent = Contact.Consent.GRANTED if consent_attested else Contact.Consent.UNKNOWN
    with transaction.atomic():
        batch = ContactImport.objects.create(
            organisation=scope.organisation,
            campus=campus,
            created_by=scope.user,
            origin=origin,
            calling_purpose=calling_purpose,
            consent_attested=consent_attested,
            consent_evidence=consent_evidence,
            rows=len(rows),
            duplicates=counts["existing"] + counts["repeated"],
            invalid=counts["invalid"],
            opted_out=counts["opted_out"],
        )
        created = 0
        for row in rows:
            if row.status != NEW:
                continue
            try:
                contact = contacts.create(
                    scope,
                    campus,
                    name=row.name,
                    phone=row.number,
                    origin=origin,
                    calling_purpose=calling_purpose,
                    consent=consent,
                    consent_evidence=consent_evidence if consent_attested else "",
                    source_import=batch,
                )
            except contacts.DuplicateContactError:
                batch.duplicates += 1
                continue
            created += 1
            if create_leads:
                Lead.objects.create(
                    organisation=scope.organisation,
                    campus=campus,
                    contact=contact,
                    subject=row.subject or default_subject,
                    created_by=scope.user,
                )
        batch.created = created
        batch.save(update_fields=["created", "duplicates"])
    audit.record(
        scope,
        "contacts.imported",
        batch,
        campus=campus,
        rows=len(rows),
        created=created,
        consent_attested=consent_attested,
    )
    return batch
