"""Inbound calls fail closed on the worker: a phone call with no assigned
employee (unknown number, paused or draft employee, Django down) never gets a
business conversation. It hears one neutral line, nothing is recorded, and the
room is deleted. No network, no real session: both are fakes."""

from types import SimpleNamespace

import pytest
from livekit import rtc

from voice_agent import inbound

from .test_employee_calls import FakeSession, inbound_settings

SIP = rtc.ParticipantKind.PARTICIPANT_KIND_SIP


def phone_caller():
    return SimpleNamespace(
        attributes={
            "sip.trunkPhoneNumber": "+914012345678",
            "sip.phoneNumber": "+919876543210",
            "sip.callID": "SCL_1",
        },
        kind=SIP,
        identity="sip_caller",
    )


class FakeVoiceSession:
    def __init__(self):
        self.started, self.said = [], []

    async def start(self, **kwargs):
        self.started.append(kwargs)

    def say(self, text, **kwargs):
        self.said.append((text, kwargs))

        class Handle:
            async def wait_for_playout(self):
                return None

        return Handle()

    def on(self, *args, **kwargs):
        return lambda fn: fn


class FakeInboundJob:
    def __init__(self):
        self.deleted, self.shutdowns, self.callbacks = [], [], []
        self.room = SimpleNamespace(
            name="call-in-1", remote_participants={}, on=lambda *a, **k: lambda f: f
        )
        self.api = SimpleNamespace(room=SimpleNamespace(delete_room=self._delete))

    async def _delete(self, request):
        self.deleted.append(request.room)

    async def connect(self):
        return None

    async def wait_for_participant(self, identity=None):
        return phone_caller()

    def add_shutdown_callback(self, callback):
        self.callbacks.append(callback)

    def shutdown(self, reason=""):
        self.shutdowns.append(reason)


@pytest.fixture
def voice(monkeypatch):
    session = FakeVoiceSession()
    monkeypatch.setattr(inbound, "build_session", lambda settings: session)
    monkeypatch.setattr(inbound, "log_callers", lambda room: None)
    return session


@pytest.mark.parametrize(
    ("status", "error"),
    [(404, None), (503, None), (200, OSError("Django is down"))],
    ids=["not routed or paused", "django refuses", "django unreachable"],
)
async def test_a_call_without_an_employee_hears_one_line_and_ends(
    voice, monkeypatch, status, error, caplog
):
    FakeSession.sent = []
    FakeSession.status, FakeSession.payload, FakeSession.error = status, {}, error
    original = inbound.fetch_profile

    async def fetch(settings, room, participant):
        return await original(settings, room, participant, session_factory=FakeSession)

    monkeypatch.setattr(inbound, "fetch_profile", fetch)
    job = FakeInboundJob()
    settings = inbound_settings()

    await inbound.run_inbound_call(
        job, settings, fallback=SimpleNamespace(purpose="business")
    )

    (started,) = voice.started
    assert started["record"] is False, "nothing is kept by LiveKit Cloud"
    ((text, options),) = voice.said
    assert text == settings.inbound_unrouted_message
    assert options == {"allow_interruptions": False}
    assert job.deleted == ["call-in-1"]
    assert job.shutdowns == ["inbound call not routed"]
    assert job.callbacks == [], "no call report: nothing was answered for a business"
    assert "9876543210" not in caplog.text


def test_the_neutral_line_names_no_business():
    message = inbound_settings().inbound_unrouted_message
    assert "Sorry" in message and "క్షమించండి" in message
    custom = inbound_settings(INBOUND_UNROUTED_MESSAGE="Please call back later.")
    assert custom.inbound_unrouted_message == "Please call back later."
