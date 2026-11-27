"""Explicit retries that retain the original attempt and recheck call permissions.

A unique database claim makes a repeated POST open the same retry. The old
record is never reset or overwritten. A failed retry can itself be retried.
"""

import logging
from copy import deepcopy

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from apps.crm import contacts, crypto
from apps.telephony import calls
from apps.telephony.models import CallRecord
from apps.tenancy import audit, hours
from apps.workforce import publishing
from apps.workforce.models import Employee

from .models import CallAttempt, CallList, CallListEntry, CallRetry

RETRY_STATUSES = (
    CallRecord.Status.FAILED,
    CallRecord.Status.NO_ANSWER,
    CallRecord.Status.REJECTED,
)
ENTRY_STATUSES = (CallListEntry.Status.FAILED, CallListEntry.Status.NO_ANSWER)
logger = logging.getLogger(__name__)


def _entry_for(call):
    return (
        call.list_entries.filter(
            call_list__organisation_id=call.organisation_id,
            call_list__campus_id=call.campus_id,
        )
        .select_related("call_list__employee", "call_list__campus")
        .first()
    )


def _existing(call=None, entry=None):
    if entry is not None:
        return CallRetry.objects.filter(source_entry=entry).first()
    return CallRetry.objects.filter(source_call=call).first()


def target_url(retry):
    if retry and retry.target_list_id:
        return reverse("call-list", args=[retry.target_list_id])
    if retry and retry.target_call_id:
        return reverse("conversation-detail", args=[retry.target_call_id])
    return ""


def _employee_block(scope, employee, campus):
    if employee is None:
        return "This call has no AI employee. Choose an employee in Make calls."
    if not scope.limit_shared(Employee.objects.all()).filter(pk=employee.pk).exists():
        return "This AI employee is no longer available in your workspace."
    if employee.live_version_id is None or not employee.taking_calls:
        return "Publish and switch on this AI employee before calling again."
    if (
        campus is not None
        and employee.campuses.exists()
        and not employee.campuses.filter(pk=getattr(campus, "pk", None)).exists()
    ):
        return "This AI employee is not assigned to this location."
    return ""


def _test_call(call):
    return (
        call.is_rehearsal or call.attempts.filter(mode=CallAttempt.Mode.TEST).exists()
    )


def entry_block(scope, entry):
    if entry.status not in ENTRY_STATUSES:
        return "Only failed or unanswered calls can be called again."
    original = entry.call_list
    if original.organisation_id != scope.organisation.pk:
        return "This call is outside your workspace."
    if not original.consent_confirmed_at:
        return "Confirm permission to call before starting a new call."
    if entry.call_id and (
        entry.call.is_active or entry.call.status not in RETRY_STATUSES
    ):
        return "This call is not eligible to be called again."
    reason = _employee_block(scope, original.employee, original.campus)
    if reason:
        return reason
    try:
        number = crypto.decrypt(entry.phone_encrypted)
        if not number:
            return "The original number is unavailable. Add it in Make calls."
        if contacts.is_opted_out(scope.organisation, crypto.lookup(number)):
            return "This number is on the do-not-call list."
    except crypto.CryptoError:
        return "The original number could not be read. Check phone encryption settings."
    return ""


def call_block(scope, call):
    if not scope.limit(CallRecord.objects.all()).filter(pk=call.pk).exists():
        return "This call is outside your workspace."
    if (
        call.direction != CallRecord.Direction.OUTBOUND
        or call.status not in RETRY_STATUSES
    ):
        return "Only failed or unanswered outgoing calls can be called again."
    entry = _entry_for(call)
    if entry:
        # A call result can arrive before its list's next polling step.
        if entry.status == CallListEntry.Status.CALLING:
            entry.status = (
                CallListEntry.Status.NO_ANSWER
                if call.status
                in (CallRecord.Status.NO_ANSWER, CallRecord.Status.REJECTED)
                else CallListEntry.Status.FAILED
            )
        return entry_block(scope, entry)
    number = call.destination_number
    if not number:
        return "The original full number was not saved. Add it in Make calls."
    employee = call.employee_version.employee if call.employee_version_id else None
    if employee is not None or not _test_call(call):
        reason = _employee_block(scope, employee, call.campus)
        if reason:
            return reason
    try:
        if contacts.is_opted_out(scope.organisation, crypto.lookup(number)):
            return "This number is on the do-not-call list."
    except crypto.CryptoError:
        return "Phone encryption settings are unavailable."
    if not _test_call(call):
        if not call.contact_id:
            return "Permission to call this number is unavailable. Confirm it in Make calls."
        if call.contact.organisation_id != scope.organisation.pk:
            return "The contact is outside your workspace."
        reason = contacts.call_block(call.contact)
        if reason:
            return reason
        if not settings.CONTACT_DIALING_ENABLED:
            return "Calling stored contacts is switched off on this installation."
        try:
            if crypto.decrypt(call.contact.phone_encrypted) != number:
                return (
                    "The contact's number changed. Start a new call from the contact."
                )
        except crypto.CryptoError:
            return "The contact's number could not be read."
    return ""


def entry_options(scope, entry):
    return _options(scope, entry=entry)


def call_options(scope, call):
    return _options(scope, call=call, entry=_entry_for(call))


def _options(scope, *, call=None, entry=None):
    if not scope.can("calls.place"):
        return {
            "can_retry": False,
            "retry_block": "",
            "retry_url": "",
            "retry_target_url": "",
        }
    existing = _existing(call, entry)
    url = target_url(existing)
    eligible = (
        (
            call.status in RETRY_STATUSES
            and call.direction == CallRecord.Direction.OUTBOUND
        )
        if call is not None
        else entry.status in ENTRY_STATUSES
    )
    block = (
        (call_block(scope, call) if call is not None else entry_block(scope, entry))
        if eligible and not existing
        else ""
    )
    return {
        "can_retry": bool(eligible and not block and not existing),
        "retry_block": block if eligible else "",
        "retry_url": reverse("conversation-retry", args=[call.pk])
        if call is not None
        else reverse("call-list-retry", args=[entry.call_list_id, entry.pk]),
        "retry_target_url": url,
        "retry_pending": bool(existing and not url),
    }


def preserved_plan(call, *, employee, campus, lead=None, caller_name="", about=""):
    """Validate availability now, then keep the script/context of the old call."""
    current = publishing.plan_call(
        employee, campus=campus, lead=lead, caller_name=caller_name, about=about
    )
    if (
        call is None
        or not call.employee_version_id
        or not call.context_snapshot.get("profile")
    ):
        return current
    if call.employee_version.employee_id != employee.pk:
        raise calls.CallError("The original AI employee is no longer available.")
    snapshot = deepcopy(call.context_snapshot)
    # Keep the original script, while respecting today's privacy choices.
    current_profile = current.snapshot["profile"]
    for key in ("transcript", "observability"):
        snapshot["profile"].pop(key, None)
        if key in current_profile:
            snapshot["profile"][key] = current_profile[key]
    snapshot["built_at"] = timezone.now().isoformat()
    snapshot["retry_of"] = str(call.pk)
    snapshot.pop("recording", None)
    return publishing.CallPlan(
        call.employee_version, {publishing.PROFILE_KEY: snapshot["profile"]}, snapshot
    )


def retry_entry(scope, entry):
    existing = _existing(entry=entry)
    if existing:
        return existing
    reason = entry_block(scope, entry)
    if reason:
        raise calls.CallError(reason)
    try:
        with transaction.atomic():
            claim = CallRetry.objects.create(
                source_entry=entry, source_call=entry.call, created_by=scope.user
            )
            old = entry.call_list
            new = CallList.objects.create(
                organisation=old.organisation,
                campus=old.campus,
                employee=old.employee,
                name=f"Call again · {entry.name or 'Previous number'}"[:120],
                purpose=old.purpose,
                created_by=scope.user,
                status=CallList.Status.RUNNING,
                consent_confirmed_at=old.consent_confirmed_at,
                consent_text=old.consent_text,
            )
            CallListEntry.objects.create(
                call_list=new,
                position=1,
                name=entry.name,
                phone_encrypted=entry.phone_encrypted,
                phone_lookup=entry.phone_lookup,
                phone_masked=entry.phone_masked,
            )
            claim.target_list = new
            claim.save(update_fields=["target_list"])
    except IntegrityError:
        claim = _existing(entry=entry)
        if claim is None:
            raise
        return claim
    audit.record(scope, "call.retried", entry, call_list=str(new.pk))
    return claim


def _budget(organisation):
    if not organisation.monthly_call_minutes:
        return
    start = timezone.localtime().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    used = (
        CallRecord.objects.filter(
            organisation=organisation, created_at__gte=start
        ).aggregate(total=Sum("duration_seconds"))["total"]
        or 0
    )
    if used >= organisation.monthly_call_minutes * 60:
        raise calls.CallError("This month's calling budget is used up.")


def retry_call(scope, call, *, gateway=None):
    existing = _existing(call=call)
    if existing:
        return existing
    reason = call_block(scope, call)
    if reason:
        raise calls.CallError(reason)
    entry = _entry_for(call)
    if entry:
        if entry.status == CallListEntry.Status.CALLING:
            entry.status = (
                CallListEntry.Status.NO_ANSWER
                if call.status
                in (CallRecord.Status.NO_ANSWER, CallRecord.Status.REJECTED)
                else CallListEntry.Status.FAILED
            )
        return retry_entry(scope, entry)
    _budget(scope.organisation)
    if call.campus is not None and not hours.is_open(call.campus):
        raise calls.CallError("The location is outside its business hours.")
    employee = call.employee_version.employee if call.employee_version_id else None
    try:
        plan = (
            preserved_plan(call, employee=employee, campus=call.campus, lead=call.lead)
            if employee
            else None
        )
        number = call.destination_number
        if call.contact_id and not _test_call(call):
            # Recheck current consent immediately before claiming a retry.
            number = contacts.number_for_call(
                call.contact, actor=scope.user, purpose="retry failed call"
            )
    except (
        publishing.PublishError,
        contacts.CallNotPermittedError,
        crypto.CryptoError,
    ) as error:
        raise calls.CallError(str(error)) from None
    try:
        with transaction.atomic():
            claim = CallRetry.objects.create(source_call=call, created_by=scope.user)
    except IntegrityError:
        return CallRetry.objects.get(source_call=call)
    try:

        def remember(record):
            claim.target_call = record
            claim.save(update_fields=["target_call"])
            if _test_call(call):
                CallAttempt.objects.create(
                    organisation=scope.organisation,
                    campus=call.campus,
                    user=scope.user,
                    mode=CallAttempt.Mode.TEST,
                    masked_number=record.masked_number,
                    employee_version=record.employee_version,
                    purpose="Retry test call",
                    outcome=CallAttempt.Outcome.PLACED,
                    call=record,
                )

        record = calls.start_call(
            user=scope.user,
            organisation=scope.organisation,
            to=number,
            contact=call.contact if not _test_call(call) else None,
            lead=call.lead,
            campus=call.campus,
            call_plan=plan,
            gateway=gateway,
            allowlisted_only=_test_call(call),
            on_created=remember,
        )
    except calls.CallError as error:
        record = getattr(error, "record", None)
        if record is None:
            # Refused before a call was created: let the user fix it and retry.
            claim.delete()
            raise
    except Exception:
        if claim.target_call_id is None:
            claim.delete()
        logger.exception(
            "Could not confirm call retry", extra={"source_call": str(call.pk)}
        )
        if claim.target_call_id is None:
            raise calls.CallError(
                "The retry could not be prepared. Please try again."
            ) from None
        # The network may have accepted this call. Keep its ID and let normal
        # status reconciliation check it instead of risking a duplicate dial.
        return claim
    claim.target_call = record
    claim.save(update_fields=["target_call"])
    audit.record(scope, "call.retried", call, retry_call=str(record.pk))
    return claim
