"""Explicit contact calls. No scheduler starts these automatically."""

from apps.crm import contacts
from apps.telephony import calls
from apps.tenancy import hours
from apps.workforce import publishing


def contact_call(*, contact, employee, user=None, lead=None, gateway=None):
    if contact.organisation_id != employee.organisation_id:
        raise calls.CallError(
            "The employee and contact must belong to the same organisation."
        )
    if (
        employee.campuses.exists()
        and not employee.campuses.filter(pk=contact.campus_id).exists()
    ):
        raise calls.CallError(
            "This employee is not assigned to the contact's location."
        )
    if not hours.is_open(contact.campus):
        raise calls.CallError("The location is outside its business hours.")
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
