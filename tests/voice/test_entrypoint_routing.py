"""Real rooms always inspect the participant before choosing a conversation.

The entrypoint is loaded with test-only settings. No LiveKit connection or model
request is made: the routing target and audio session are stand-ins.
"""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from voice_agent import config, inbound, observability

from .fakes import make_settings
from .test_employee_calls import inbound_settings
from .test_inbound_fallback import FakeInboundJob, FakeVoiceSession


@pytest.fixture
def entrypoint(monkeypatch):
    monkeypatch.setattr(config, "load_settings", lambda: make_settings())
    monkeypatch.setattr(observability, "configure_logging", lambda settings: None)
    return importlib.import_module("voice_agent.agent")


def job(*, fake=False, simulation=False):
    ctx = FakeInboundJob()
    ctx.job = SimpleNamespace(metadata="")
    ctx.is_fake_job = lambda: fake
    ctx.simulation_context = lambda: (
        SimpleNamespace(userdata=lambda: {}) if simulation else None
    )
    return ctx


@pytest.mark.parametrize("configured", [False, True])
async def test_real_jobs_check_the_caller_even_without_a_routing_url(
    entrypoint, monkeypatch, configured
):
    settings = inbound_settings() if configured else make_settings()
    monkeypatch.setattr(entrypoint, "settings", settings)
    inbound = AsyncMock()
    monkeypatch.setattr(entrypoint, "run_inbound_call", inbound)

    def no_generic_session(settings):
        pytest.fail("A real room bypassed the inbound participant check.")

    monkeypatch.setattr(entrypoint, "build_session", no_generic_session)
    ctx = job()
    await entrypoint.entrypoint(ctx)
    inbound.assert_awaited_once_with(ctx, settings, None)


@pytest.mark.parametrize("fake, simulation", [(True, False), (False, True)])
async def test_debugger_and_simulations_keep_the_local_session_path(
    entrypoint, monkeypatch, fake, simulation
):
    monkeypatch.setattr(entrypoint, "settings", inbound_settings())
    inbound = AsyncMock()
    monkeypatch.setattr(entrypoint, "run_inbound_call", inbound)
    session = FakeVoiceSession()
    monkeypatch.setattr(entrypoint, "build_session", lambda settings: session)
    monkeypatch.setattr(entrypoint, "build_audio_input", lambda settings: None)
    monkeypatch.setattr(entrypoint, "attach_session_observers", lambda *a, **k: None)
    monkeypatch.setattr(entrypoint, "recording_fixture", lambda *a: None)
    monkeypatch.setattr(entrypoint, "log_callers", lambda room: None)
    monkeypatch.setattr(entrypoint, "greet", lambda *a, **k: None)

    await entrypoint.entrypoint(job(fake=fake, simulation=simulation))

    inbound.assert_not_awaited()
    assert len(session.started) == 1


async def test_a_real_phone_call_without_routing_configuration_is_declined(
    entrypoint, monkeypatch
):
    settings = make_settings()
    monkeypatch.setattr(entrypoint, "settings", settings)
    session = FakeVoiceSession()
    monkeypatch.setattr(inbound, "build_session", lambda settings: session)
    monkeypatch.setattr(inbound, "log_callers", lambda room: None)
    ctx = job()

    await entrypoint.entrypoint(ctx)

    assert session.started[0]["record"] is False
    assert session.said == [
        (settings.inbound_unrouted_message, {"allow_interruptions": False})
    ]
    assert ctx.deleted == [ctx.room.name]
    assert ctx.shutdowns == ["inbound call not routed"]
    assert ctx.callbacks == []
