"""Inbound calls: which employee answers a number, and who is calling.

The voice worker asks `answer()` (through a signed request, see
apps/telephony/views.inbound_context) when a phone call arrives. The answer is
the published version of the employee assigned to the dialled number, with
that number's campus knowledge, and a call record is created for the call.

The caller's number is used only to recognise a known contact: it is matched
through the keyed hash, stored encrypted on the call record, and never shown
or logged. A caller on the do-not-call list is still answered: they called us.
"""

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.crm import contacts, crypto
from apps.crm.models import Lead
from apps.crm.phones import InvalidPhoneError, normalize_line
from apps.telephony.masking import mask_phone
from apps.telephony.models import CallRecord
from apps.tenancy import hours
from apps.workforce import publishing

from .models import CallResult, MissedCall, PhoneNumber

logger = logging.getLogger(__name__)


def route_for(number: str) -> PhoneNumber | None:
    """The routed number, if calls to it may be answered by an employee now."""
    try:
        e164 = normalize_line(number)
    except InvalidPhoneError:
        return None
    route = (
        PhoneNumber.objects.select_related(
            "employee__live_version", "employee__organisation", "campus"
        )
        .filter(number=e164, routing_enabled=True)
        .first()
    )
    if route is None or route.employee is None:
        return None
    employee = route.employee
    if employee.organisation_id != route.organisation_id:
        return None
    if employee.live_version is None or not employee.taking_calls:
        return None
    return route


def problems(route: PhoneNumber) -> list[str]:
    """Why calls to this number would not be answered by an employee."""
    found = []
    if not route.routing_enabled:
        found.append("Routing is switched off for this number.")
    employee = route.employee
    if employee is None:
        found.append("No AI employee is assigned.")
    elif employee.organisation_id != route.organisation_id:
        found.append("The assigned employee belongs to another organisation.")
    elif employee.live_version is None:
        found.append(f"{employee.name} has not been published.")
    elif not employee.taking_calls:
        found.append(f"{employee.name} is paused.")
    return found


def answer(*, room: str, called: str, caller: str) -> dict | None:
    """The job metadata for an inbound call, or None when the number is not
    routed. Creates (or, for a repeated request, reuses) the call record."""
    existing = CallRecord.objects.filter(room=room).first()
    if existing is not None:
        if existing.direction != CallRecord.Direction.INBOUND:
            return None
        return {publishing.PROFILE_KEY: existing.context_snapshot.get("profile", {})}
    route = route_for(called)
    if route is None:
        _record_unanswered(room, called, caller)
        logger.info("inbound call to an unrouted number", extra={"room": room})
        return None
    organisation = route.organisation
    contact = lead = None
    try:
        # A caller may be on a landline; only mobiles can match a contact.
        caller_number = normalize_line(caller)
    except InvalidPhoneError:
        caller_number = ""
    lookup = encrypted = ""
    if caller_number and crypto.configured():
        lookup = crypto.lookup(caller_number)
        encrypted = crypto.encrypt(caller_number)
        contact = contacts.find(organisation, caller_number)
        if contact is not None and contact.is_erased:
            contact = None
        if contact is not None:
            lead = (
                Lead.objects.filter(contact=contact, stage__in=Lead.OPEN_STAGES)
                .order_by("-created_at")
                .first()
            )
    campus = route.campus or (contact.campus if contact else None)
    plan = publishing.plan_call(
        route.employee,
        campus=campus,
        lead=lead,
        caller_name=contact.name if contact else "",
    )
    after_hours = (
        campus is not None and hours.has_hours(campus) and not hours.is_open(campus)
    )
    if after_hours:
        notice = after_hours_notice(campus)
        plan.metadata[publishing.PROFILE_KEY]["notice"] = notice
        plan.snapshot["profile"]["notice"] = notice
        plan.snapshot["after_hours"] = True
    try:
        with transaction.atomic():
            record = CallRecord.objects.create(
                direction=CallRecord.Direction.INBOUND,
                room=room,
                masked_number=mask_phone(caller_number)
                if caller_number
                else "(hidden)",
                status=CallRecord.Status.RINGING,
                status_source=CallRecord.Source.WORKER,
                organisation=organisation,
                campus=campus,
                lead=lead,
                contact=contact,
                employee_version=plan.version,
                context_snapshot={**plan.snapshot, "number": route.number},
                remote_lookup=lookup,
                remote_encrypted=encrypted,
            )
    except IntegrityError:
        # Two requests for one room at once: the other one created it.
        record = CallRecord.objects.get(room=room)
        return {publishing.PROFILE_KEY: record.context_snapshot.get("profile", {})}
    CallResult.objects.get_or_create(call=record)
    if after_hours:
        _missed(record, MissedCall.Reason.AFTER_HOURS, "A message was taken.")
    logger.info(
        "inbound call routed",
        extra={"call_id": str(record.pk), "employee_version": plan.version.number},
    )
    return plan.metadata


def after_hours_notice(campus) -> str:
    """What the employee is told when the campus is closed."""
    opens = hours.next_open(campus)
    when = f" It opens {opens:%A at %H:%M}." if opens else ""
    extra = f" {campus.after_hours_message}" if campus.after_hours_message else ""
    return (
        f"{campus.name} is closed now.{when} Take a message: their name, what "
        f"they need and a good time to call back. Say the team will call back.{extra}"
    )


def _missed(record: CallRecord, reason: str, detail: str = "") -> MissedCall:
    from apps.operations.jobs import notify
    from apps.operations.models import Notification

    missed, _ = MissedCall.objects.get_or_create(
        call=record, defaults={"reason": reason, "detail": detail[:200]}
    )
    if record.organisation is not None:
        who = record.contact.name if record.contact else record.masked_number
        notify(
            record.organisation,
            Notification.Kind.MISSED_CALL,
            f"Call back {who}: {missed.get_reason_display().lower()}.",
            url=f"/conversations/{record.pk}/",
            key=f"missed:{record.pk}",
            campus=record.campus,
        )
    return missed


def _record_unanswered(room: str, called: str, caller: str) -> None:
    """A call to one of our numbers that no ready employee could take is kept
    as a missed caller, so someone calls back. Unknown numbers are ignored."""
    try:
        number = (
            PhoneNumber.objects.select_related("employee", "campus")
            .filter(number=normalize_line(called))
            .first()
        )
    except InvalidPhoneError:
        number = None
    if number is None or CallRecord.objects.filter(room=room).exists():
        return
    try:
        caller_number = normalize_line(caller)
    except InvalidPhoneError:
        caller_number = ""
    contact = None
    lookup = encrypted = ""
    if caller_number and crypto.configured():
        lookup, encrypted = crypto.lookup(caller_number), crypto.encrypt(caller_number)
        contact = contacts.find(number.organisation, caller_number)
        if contact is not None and contact.is_erased:
            contact = None
    reasons = problems(number)
    try:
        with transaction.atomic():
            record = CallRecord.objects.create(
                direction=CallRecord.Direction.INBOUND,
                room=room,
                masked_number=mask_phone(caller_number)
                if caller_number
                else "(hidden)",
                status=CallRecord.Status.FAILED,
                status_source=CallRecord.Source.DJANGO,
                failure_reason=" ".join(reasons)[:200],
                organisation=number.organisation,
                campus=number.campus or (contact.campus if contact else None),
                contact=contact,
                remote_lookup=lookup,
                remote_encrypted=encrypted,
                ended_at=timezone.now(),
            )
    except IntegrityError:
        return
    _missed(record, MissedCall.Reason.NOT_ROUTED, " ".join(reasons))
