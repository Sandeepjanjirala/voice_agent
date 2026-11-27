"""Isolated recording controls for simulated and local console conversations.

The real tools and their state transitions run; media capture and uploads do
not. Audio recording is verified separately against RecorderIO in unit tests.
These fixtures are never selected for dispatched business phone calls.
"""

import time

from .call_reporting import CallReporter
from .recording import CallRecorder


class SimulatedRecorder(CallRecorder):
    async def _start(self) -> None:
        self._recording_from = time.time()
        self.state = "recording"
        self._report(state="started", offset=0)

    async def finish(self) -> bool:
        await self.flush()
        return False  # No media is captured or sent by this fixture.


def recording_fixture(session, profile):
    if profile is None or not (
        profile.recording_notice or profile.recording_authorization == "demo"
    ):
        return None
    return SimulatedRecorder(
        session=session,
        room="recording-simulation",
        reporter=CallReporter(None, None, "recording-simulation"),
        upload_url=None,
        secret=None,
        conversation_started_at=time.time(),
        authorization=profile.recording_authorization or "verbal",
    )


async def grade_recording(simulation) -> None:
    expected = simulation.userdata().get("recording_state")
    if expected is None:
        return
    try:
        recorder = simulation.job_context.primary_session.userdata["recording"]
    except (RuntimeError, ValueError, KeyError, TypeError):
        simulation.fail("The recording scenario did not initialize its state.")
        return
    if recorder is None or recorder.state != expected:
        simulation.fail(f"Expected recording state {expected!r} was not reached.")
