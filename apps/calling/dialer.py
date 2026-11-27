"""Test calls from the staff calls API: they ring only the approved numbers in
OUTBOUND_ALLOWLIST_NUMBERS, chosen by position, so a request never carries a
phone number. Calls to anyone else go through Make calls (calllists.py).

Every test attempt inside an organisation is kept as a CallAttempt.
"""

from apps.telephony import calls
from apps.telephony.masking import mask_phone
from apps.workforce import publishing

from .models import CallAttempt

# --- test calls ------------------------------------------------------------------------


def test_numbers() -> list[tuple[int, str]]:
    """(slot, full number) for each approved test destination."""
    try:
        outbound, _ = calls.outbound_context()
    except calls.CallError:
        return []
    return list(enumerate(_approved(outbound)))


def _approved(outbound) -> list[str]:
    numbers = list(outbound.policy.numbers)
    if outbound.allowed_number and outbound.allowed_number not in numbers:
        numbers.append(outbound.allowed_number)
    return numbers


def test_destination(slot) -> str:
    """The approved number in that position. Raises calls.CallError."""
    outbound, _ = calls.outbound_context()
    numbers = _approved(outbound)
    # A whole number or its digits; a float, bool or sign is not a position.
    if isinstance(slot, str) and slot.isascii() and slot.isdigit():
        try:
            index = int(slot)
        except ValueError:
            index = -1
    elif isinstance(slot, int) and not isinstance(slot, bool):
        index = slot
    else:
        index = -1
    if not 0 <= index < len(numbers):
        raise calls.CallError(
            "Choose one of the approved test numbers. Test calls go only to "
            "numbers in OUTBOUND_ALLOWLIST_NUMBERS."
        )
    return numbers[index]


def place_test(scope, slot, *, user, employee=None, gateway=None) -> CallAttempt:
    """A test call to an approved number. Raises calls.CallError (recorded)."""
    # A platform superuser outside any organisation can still test the line;
    # the attempt is then not stored (an attempt belongs to an organisation).
    organisation = getattr(scope, "organisation", None)
    keep = organisation is not None
    attempt = CallAttempt(
        organisation=organisation,
        user=user if getattr(user, "is_authenticated", False) else None,
        mode=CallAttempt.Mode.TEST,
        employee_version=employee.live_version if employee else None,
        purpose="Test call",
    )
    try:
        number = test_destination(slot)
        attempt.masked_number = mask_phone(number)
        plan = publishing.plan_call(employee, rehearsal=True) if employee else None
        record = calls.start_call(
            user=user,
            organisation=organisation,
            to=number,
            call_plan=plan,
            gateway=gateway,
            allowlisted_only=True,
        )
    except publishing.PublishError as error:
        attempt.outcome, attempt.reason = CallAttempt.Outcome.REFUSED, str(error)[:300]
        if keep:
            attempt.save()
        raise calls.CallError(str(error)) from None
    except calls.CallError as error:
        attempt.outcome, attempt.reason = CallAttempt.Outcome.REFUSED, str(error)[:300]
        if keep:
            attempt.save()
        raise
    attempt.outcome, attempt.call = CallAttempt.Outcome.PLACED, record
    if keep:
        attempt.save()
    return attempt
