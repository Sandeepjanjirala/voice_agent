"""Explicit contact calls. No scheduler starts these automatically."""

from django.db import transaction
from django.utils import timezone

from apps.crm import contacts
from apps.telephony import calls
from apps.tenancy import hours
from apps.workforce import publishing
from dialing import DialingPolicyError

from . import queue
from .models import Campaign, QueuedCall


def contact_call(*, contact, employee, user=None, lead=None, gateway=None):
    if contact.organisation_id != employee.organisation_id:
        raise calls.CallError(
            "The employee and contact must belong to the same organisation."
        )
    if (
        employee.campuses.exists()
        and not employee.campuses.filter(pk=contact.campus_id).exists()
    ):
        raise calls.CallError("This employee is not assigned to the contact's campus.")
    if not hours.is_open(contact.campus):
        raise calls.CallError("The campus is outside its business hours.")
    try:
        plan = publishing.plan_call(employee, campus=contact.campus, lead=lead)
        number = contacts.number_for_call(
            contact, actor=user, purpose="outbound follow-up"
        )
    except (publishing.PublishError, contacts.CallNotPermittedError) as error:
        raise calls.CallError(str(error)) from None
    return calls.start_call(
        user=user,
        to=number,
        contact=contact,
        lead=lead,
        campus=contact.campus,
        call_plan=plan,
        gateway=gateway,
    )


def run_campaign_entry(entry_id, *, user=None, gateway=None):
    """Dispatch one due entry, never substitute a test number or automatically retry.

    Serialize on the campaign, reserve the attempt before network I/O, and mark
    it consumed even if dispatch is uncertain. An operator must review failures.
    """
    with transaction.atomic():
        entry = QueuedCall.objects.select_for_update().get(pk=entry_id)
        if not entry.campaign_id:
            raise calls.CallError("This entry does not belong to a campaign.")
        campaign = Campaign.objects.select_for_update().get(pk=entry.campaign_id)
        if campaign.status != Campaign.Status.READY or not campaign.consent_attested:
            raise calls.CallError("The campaign must be ready and consent confirmed.")
        if (
            entry.organisation_id != campaign.organisation_id
            or entry.campus_id != campaign.campus_id
            or entry.contact.organisation_id != campaign.organisation_id
            or entry.contact.campus_id != campaign.campus_id
        ):
            raise calls.CallError(
                "The queued contact is outside the campaign's campus."
            )
        rules, now = queue.Rules.for_campaign(campaign), timezone.now()
        reason = queue.employee_problem(campaign.employee) or queue._skip_reason(
            entry, rules, now
        )
        if reason:
            raise calls.CallError(reason)
        if entry.status == queue.Status.DONE:
            raise calls.CallError(
                "This entry has already been dispatched; no automatic retry."
            )
        if entry.due_at and entry.due_at > now:
            raise calls.CallError("This queued call is not due yet.")
        if queue.next_slot(rules, now) > now or not hours.is_open(entry.campus):
            raise calls.CallError("The campaign is outside its calling hours.")
        used = campaign.queue.filter(
            status=queue.Status.DONE, checked_at__date=timezone.localdate(now)
        ).count()
        if used >= rules.daily_limit:
            raise calls.CallError("The campaign daily calling limit has been reached.")
        # Check the policy and consent before consuming an attempt.
        try:
            number = contacts.number_for_call(
                entry.contact, actor=user, purpose="campaign"
            )
            outbound, plan = calls.outbound_context()
            outbound.policy.validate(number)
        except (contacts.CallNotPermittedError, DialingPolicyError) as error:
            raise calls.CallError(str(error)) from None
        if not plan.ready:
            raise calls.CallNotReadyError("Outbound calling is not ready.")
        entry.status = queue.Status.DONE
        entry.attempts += 1
        entry.checked_at = now
        entry.reason = "Dispatch reserved; review call records before retrying."
        entry.save(update_fields=["status", "attempts", "checked_at", "reason"])
    return contact_call(
        contact=entry.contact,
        employee=campaign.employee,
        user=user,
        lead=entry.lead,
        gateway=gateway,
    )
