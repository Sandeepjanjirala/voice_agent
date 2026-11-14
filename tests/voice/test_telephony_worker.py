"""Phone-caller detection and log-safe caller summaries in the voice worker."""

from types import SimpleNamespace

from livekit import rtc

from voice_agent.telephony import (
    describe_caller,
    is_phone_caller,
    log_callers,
    mask_phone,
)

CALLER = "+919876543210"


def sip_participant(**attributes: str) -> SimpleNamespace:
    return SimpleNamespace(
        kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
        attributes=attributes,
        disconnect_reason=rtc.DisconnectReason.CLIENT_INITIATED,
    )


def web_participant() -> SimpleNamespace:
    return SimpleNamespace(
        kind=rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
        attributes={},
        disconnect_reason=rtc.DisconnectReason.CLIENT_INITIATED,
    )


class FakeRoom:
    def __init__(self, *participants) -> None:
        self.remote_participants = dict(enumerate(participants))
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler


def test_numbers_are_masked() -> None:
    assert mask_phone(CALLER) == "+91********10"
    assert mask_phone(None) == "(hidden)"
    assert mask_phone("12345") == "*****"


def test_sip_and_browser_participants_are_told_apart() -> None:
    assert is_phone_caller(sip_participant())
    assert not is_phone_caller(web_participant())
    assert describe_caller(web_participant()) == {"channel": "web"}


def test_caller_summary_never_holds_a_full_number() -> None:
    summary = describe_caller(
        sip_participant(
            **{
                "sip.phoneNumber": CALLER,
                "sip.trunkPhoneNumber": "+918045550123",
                "sip.callID": "SCL_123",
                "sip.trunkID": "ST_abc",
            }
        )
    )

    assert summary["channel"] == "sip"
    assert summary["sip_call_id"] == "SCL_123"
    assert CALLER not in str(summary)
    assert "+918045550123" not in str(summary)


def test_hidden_caller_number_is_handled() -> None:
    assert describe_caller(sip_participant())["caller"] == "(hidden)"


def test_joins_and_hangups_are_logged_masked(caplog) -> None:
    caller = sip_participant(**{"sip.phoneNumber": CALLER})
    room = FakeRoom(caller)

    with caplog.at_level("INFO", logger="voice_agent"):
        log_callers(room)
        room.handlers["participant_disconnected"](caller)

    joined, left = caplog.records
    assert (joined.message, joined.channel) == ("participant joined", "sip")
    assert left.disconnect_reason == "CLIENT_INITIATED"
    assert all(CALLER not in str(record.__dict__) for record in caplog.records)
    assert set(room.handlers) == {"participant_connected", "participant_disconnected"}
