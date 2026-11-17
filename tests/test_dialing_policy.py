import pytest

from dialing import DialingPolicyError, load_policy


def test_allowlist_is_default_and_accepts_multiple_dynamic_numbers():
    policy = load_policy({"OUTBOUND_ALLOWLIST_NUMBERS": "+919000000001, +919000000002"})
    for number in policy.numbers:
        assert policy.validate(number) == number
        assert number not in repr(policy)
    with pytest.raises(DialingPolicyError, match="blocked"):
        policy.validate("+919000000003")


def test_an_empty_allowlist_falls_back_to_the_old_test_number():
    policy = load_policy(
        {
            "OUTBOUND_ALLOWLIST_NUMBERS": "",
            "OUTBOUND_TEST_ALLOWED_NUMBER": "+919000000001",
        }
    )
    assert policy.validate("+919000000001") == "+919000000001"
    with pytest.raises(DialingPolicyError):
        policy.validate("+919000000002")


def test_the_allowlist_wins_over_the_old_test_number():
    policy = load_policy(
        {
            "OUTBOUND_ALLOWLIST_NUMBERS": "+919000000002",
            "OUTBOUND_TEST_ALLOWED_NUMBER": "+919000000001",
        }
    )
    assert policy.numbers == ("+919000000002",)


def test_with_no_numbers_nothing_can_be_dialled():
    with pytest.raises(DialingPolicyError, match="not in OUTBOUND_ALLOWLIST_NUMBERS"):
        load_policy({}).validate("+919000000001")


@pytest.mark.parametrize("number", ["+919000000003", "+442079460123"])
def test_live_accepts_dynamic_e164(number):
    assert load_policy({"OUTBOUND_DIALING_MODE": "live"}).validate(number) == number


@pytest.mark.parametrize("mode", ["live", "allowlist"])
@pytest.mark.parametrize(
    "number",
    [
        None,
        123,
        "",
        "9000000001",
        "+0123456789",
        "+919000000001\n",
        "+91900 000001",
        "+1234567890123456",
    ],
)
def test_invalid_destinations_never_dial(mode, number):
    with pytest.raises(DialingPolicyError):
        load_policy({"OUTBOUND_DIALING_MODE": mode}).validate(number)


def test_unknown_mode_fails_closed():
    with pytest.raises(DialingPolicyError):
        load_policy({"OUTBOUND_DIALING_MODE": "LIVE"})
