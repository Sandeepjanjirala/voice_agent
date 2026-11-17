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

from apps.crm import contacts, crypto
from apps.crm.models import Lead
from apps.crm.phones import InvalidPhoneError, normalize_line
from apps.telephony.masking import mask_phone
from apps.telephony.models import CallRecord
from apps.workforce import publishing

from .models import CallResult, PhoneNumber

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
    logger.info(
        "inbound call routed",
        extra={"call_id": str(record.pk), "employee_version": plan.version.number},
    )
    return plan.metadata
