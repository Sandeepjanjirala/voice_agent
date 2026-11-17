"""Running an approved action: the one path for staff, test runs and calls.

`run()` checks, in order, that the action belongs to the organisation, is
switched on, has a working integration, and (after a call) was allowed for
that employee version; validates the inputs against the action's schema; then
carries it out through its provider. Every attempt is logged as an ActionRun
with its result, and a failure is announced. Nothing here runs code or
requests a URL that an admin did not set up.
"""

import time
from dataclasses import dataclass
from datetime import date, datetime

from django.utils import timezone

from apps.crm.models import FollowUp, Lead
from apps.telephony.masking import mask_phones_in

from . import providers, schema
from .models import ActionRun, ApprovedAction, Integration

Kind = ApprovedAction.Kind
Status = ActionRun.Status

# The inputs each kind takes, unless the admin gives a schema (REST only).
DEFAULT_SCHEMAS = {
    Kind.CHECK_AVAILABILITY: {
        "properties": {"date": {"type": "string", "format": "date"}},
        "required": ["date"],
    },
    Kind.BOOK_APPOINTMENT: {
        "properties": {
            "start": {"type": "string", "format": "date-time"},
            "title": {"type": "string", "maxLength": 120},
        },
        "required": ["start"],
    },
    Kind.WHATSAPP_TEMPLATE: {"properties": {}, "required": []},
    Kind.UPDATE_LEAD: {
        "properties": {
            "stage": {"type": "string", "enum": list(Lead.Stage.values)},
            "note": {"type": "string", "maxLength": 300},
        },
        "required": ["stage"],
    },
    Kind.CREATE_FOLLOW_UP: {
        "properties": {
            "title": {"type": "string", "maxLength": 160},
            "due": {"type": "string", "format": "date-time"},
        },
        "required": ["title"],
    },
    Kind.ESCALATE: {
        "properties": {"reason": {"type": "string", "maxLength": 200}},
        "required": [],
    },
}
# Which integration kinds each action kind can use.
INTEGRATION_KINDS = {
    Kind.CHECK_AVAILABILITY: (
        Integration.Kind.CAMPUS_CALENDAR,
        Integration.Kind.GOOGLE_CALENDAR,
    ),
    Kind.BOOK_APPOINTMENT: (
        Integration.Kind.CAMPUS_CALENDAR,
        Integration.Kind.GOOGLE_CALENDAR,
    ),
    Kind.WHATSAPP_TEMPLATE: (Integration.Kind.WHATSAPP,),
    Kind.REST_REQUEST: (Integration.Kind.REST_API,),
}
NEEDS_LEAD = (
    Kind.WHATSAPP_TEMPLATE,
    Kind.UPDATE_LEAD,
    Kind.CREATE_FOLLOW_UP,
    Kind.ESCALATE,
)


def schema_for(action: ApprovedAction) -> dict:
    if action.kind == Kind.REST_REQUEST:
        return action.input_schema or {"properties": {}, "required": []}
    return DEFAULT_SCHEMAS[action.kind]


@dataclass
class Context:
    organisation: object
    trigger: str = ActionRun.Trigger.STAFF
    actor: object = None
    call: object = None
    lead: object = None
    # After a call: the action keys the employee version was allowed.
    allowed: tuple | None = None


class RefusedError(Exception):
    pass


def _masked(value):
    if isinstance(value, str):
        return mask_phones_in(value)[:500]
    if isinstance(value, dict):
        return {key: _masked(item) for key, item in list(value.items())[:30]}
    if isinstance(value, list):
        return [_masked(item) for item in value[:30]]
    return value


def _campus(action, ctx):
    if ctx.lead is not None:
        return ctx.lead.campus
    campus_id = action.settings.get("campus")
    if campus_id:
        from apps.tenancy.models import Campus

        return Campus.objects.filter(
            pk=campus_id, organisation=action.organisation
        ).first()
    return None


def _check(action: ApprovedAction, ctx: Context) -> None:
    if action.organisation_id != ctx.organisation.pk:
        raise RefusedError("This action belongs to another organisation.")
    if not action.is_enabled:
        raise RefusedError("This action is switched off.")
    if ctx.allowed is not None and action.employee_key not in ctx.allowed:
        raise RefusedError("This employee version was not allowed this action.")
    needed = INTEGRATION_KINDS.get(action.kind)
    if needed:
        integration = action.integration
        if integration is None or integration.kind not in needed:
            raise RefusedError("The action has no suitable integration.")
        if not integration.is_active:
            raise RefusedError(f"The integration “{integration.name}” is switched off.")
        if integration.organisation_id != action.organisation_id:
            raise RefusedError("The integration belongs to another organisation.")
    if action.kind in NEEDS_LEAD and ctx.lead is None:
        raise RefusedError("This action needs a lead.")
    if ctx.lead is not None and ctx.lead.organisation_id != ctx.organisation.pk:
        raise RefusedError("The lead belongs to another organisation.")


def _perform(action: ApprovedAction, inputs: dict, ctx: Context) -> providers.Outcome:
    kind, integration, lead = action.kind, action.integration, ctx.lead
    if kind in (Kind.CHECK_AVAILABILITY, Kind.BOOK_APPOINTMENT):
        campus = _campus(action, ctx)
        if campus is None:
            raise RefusedError(
                "A campus is needed: run it from a lead, or set one on the action."
            )
        google = integration.kind == Integration.Kind.GOOGLE_CALENDAR
        if kind == Kind.CHECK_AVAILABILITY:
            day = date.fromisoformat(inputs["date"])
            if google:
                return providers.google_availability(integration, campus, day)
            return providers.campus_availability(campus, day)
        start = datetime.fromisoformat(inputs["start"])
        title = inputs.get("title") or (
            f"Visit: {lead.contact.name}" if lead else "Appointment"
        )
        if google:
            return providers.google_book(integration, campus, start, title, lead)
        outcome = providers.campus_book(campus, start, title, lead)
        from . import webhooks

        webhooks.emit(
            action.organisation,
            "appointment.booked",
            {
                "appointment": outcome.output.get("appointment"),
                "campus": campus.name,
                "start": outcome.output.get("start"),
                "lead": str(lead.pk) if lead else None,
            },
        )
        return outcome
    if kind == Kind.WHATSAPP_TEMPLATE:
        values = {
            "contact_name": lead.contact.name,
            "lead_subject": lead.subject,
            "campus_name": lead.campus.name,
            "organisation_name": action.organisation.name,
        }
        parameters = [
            values.get(name, "") for name in action.settings.get("parameters", [])
        ]
        return providers.whatsapp_template(
            integration, action, lead.contact, parameters
        )
    if kind == Kind.REST_REQUEST:
        return providers.rest_request(integration, action, inputs)
    if kind == Kind.UPDATE_LEAD:
        before = lead.stage
        lead.stage = inputs["stage"]
        lead.closed_at = (
            None
            if lead.stage in Lead.OPEN_STAGES
            else (lead.closed_at or timezone.now())
        )
        lead.save(update_fields=["stage", "closed_at", "updated_at"])
        if inputs.get("note"):
            lead.notes.create(text=inputs["note"], author=ctx.actor)
        from . import webhooks

        webhooks.lead_stage_changed(lead, before)
        return providers.Outcome(
            Status.SUCCEEDED, f"Lead moved to {lead.get_stage_display()}."
        )
    if kind == Kind.CREATE_FOLLOW_UP:
        due = (
            datetime.fromisoformat(inputs["due"])
            if inputs.get("due")
            else timezone.now()
        )
        if timezone.is_naive(due):
            due = timezone.make_aware(due)
        FollowUp.objects.create(
            lead=lead,
            organisation=lead.organisation,
            campus=lead.campus,
            title=inputs["title"],
            due_at=due,
            assigned_to=lead.assigned_to,
            created_by=ctx.actor,
        )
        return providers.Outcome(
            Status.SUCCEEDED, f"Follow-up added: {inputs['title']}."
        )
    if kind == Kind.ESCALATE:
        return _escalate(action, inputs, ctx)
    raise RefusedError(f"Unknown action kind {kind}.")


def _escalate(action, inputs, ctx) -> providers.Outcome:
    """Hand the caller over to a person. A live transfer needs the phone line,
    which is not live, so a person is asked to call back instead."""
    from apps.operations.jobs import notify
    from apps.operations.models import Notification

    lead = ctx.lead
    reason = inputs.get("reason") or "The caller asked to speak to a person."
    owner = lead.campus.escalation_user or lead.assigned_to
    FollowUp.objects.create(
        lead=lead,
        organisation=lead.organisation,
        campus=lead.campus,
        title=f"Call back: {reason}"[:160],
        due_at=timezone.now(),
        assigned_to=owner,
        created_by=ctx.actor,
    )
    notify(
        lead.organisation,
        Notification.Kind.ESCALATION,
        f"{lead.contact.name} wants to speak to a person: {reason}",
        url=f"/leads/{lead.pk}/",
        campus=lead.campus,
        user=owner,
    )
    return providers.Outcome(
        Status.SUCCEEDED,
        "A person was asked to call back. A live transfer is not available until "
        "the phone line is live.",
        {
            "transfer": "not available",
            "assigned_to": owner.get_username() if owner else "",
        },
    )


def run(action: ApprovedAction, inputs: dict, ctx: Context) -> ActionRun:
    """Carry out one action and log it. Never raises for a business failure."""
    started = time.monotonic()
    shown_inputs = _masked(inputs if isinstance(inputs, dict) else {})
    try:
        _check(action, ctx)
        clean = schema.validate(schema.check_schema(schema_for(action)), inputs)
        outcome = _perform(action, clean, ctx)
    except (RefusedError, schema.SchemaError) as error:
        outcome = providers.Outcome(Status.REFUSED, str(error))
    except (providers.ProviderError, providers.safety.UnsafeRequestError) as error:
        outcome = providers.Outcome(Status.FAILED, str(error))
    except providers.transport.TransportError as error:
        outcome = providers.Outcome(Status.FAILED, str(error))
    record = ActionRun.objects.create(
        organisation=ctx.organisation,
        action=action,
        action_key=action.employee_key,
        action_label=action.label,
        kind=action.kind,
        trigger=ctx.trigger,
        actor=ctx.actor if getattr(ctx.actor, "is_authenticated", False) else None,
        call=ctx.call,
        lead=ctx.lead,
        status=outcome.status,
        inputs=shown_inputs,
        output=_masked(outcome.output),
        message=mask_phones_in(outcome.message)[:300],
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    if outcome.status == Status.FAILED:
        from apps.operations.jobs import notify
        from apps.operations.models import Notification

        notify(
            ctx.organisation,
            Notification.Kind.ACTION,
            f"“{action.label}” failed: {record.message}",
            url="/integrations/runs/",
        )
    return record
