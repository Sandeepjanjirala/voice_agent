"""Recording a call: both sides, only after a yes, and safely delivered.

One test runs LiveKit's real session recorder on synthetic audio (a tone for
the person, another for the AI employee) and decodes the file it writes: the
person must be on the left channel, the AI on the right, and an interrupted
reply must stop where it was cut off. The rest use a fake recorder and a fake
HTTP session. No network, no LiveKit room.
"""

import asyncio
import json
import time
from types import SimpleNamespace

import av
import numpy as np
import pytest
from livekit import rtc
from livekit.agents.voice import io
from livekit.agents.voice.recorder_io import RecorderIO

from voice_agent import recording
from voice_agent.assistant import greet
from voice_agent.call_reporting import CallReporter
from voice_agent.profile import parse_profile, profile_instructions
from voice_agent.recording import CallRecorder, sign_upload

from .fakes import make_settings
from .test_call_reporting import SECRET, FakeSession

RATE = 48_000
FRAME = 960  # 20 ms
UPLOAD_URL = "http://127.0.0.1:8000/api/telephony/call-recording/"


def tone(hz: float, frames: int) -> list[rtc.AudioFrame]:
    out = []
    for index in range(frames):
        t = (np.arange(FRAME) + index * FRAME) / RATE
        pcm = (np.sin(2 * np.pi * hz * t) * 12000).astype(np.int16)
        out.append(rtc.AudioFrame(pcm.tobytes(), RATE, 1, FRAME))
    return out


class ToneInput(io.AudioInput):
    """The person: frames arrive in real time."""

    def __init__(self, frames):
        super().__init__(label="person")
        self._frames = list(frames)

    async def __anext__(self):
        if not self._frames:
            raise StopAsyncIteration
        await asyncio.sleep(FRAME / RATE)
        return self._frames.pop(0)


class Sink(io.AudioOutput):
    """Where the AI's audio would go to the phone line."""

    def __init__(self):
        super().__init__(
            label="line", capabilities=io.AudioOutputCapabilities(pause=True)
        )
        self.frames = []

    async def capture_frame(self, frame):
        await super().capture_frame(frame)
        self.frames.append(frame)

    def flush(self):
        super().flush()

    def clear_buffer(self):
        pass


def fake_session(person_frames):
    return SimpleNamespace(
        input=SimpleNamespace(audio=ToneInput(person_frames)),
        output=SimpleNamespace(audio=Sink()),
    )


def reporter(sent):
    class Recording(CallReporter):
        async def send(self, event, **fields):
            sent.append((event, fields))
            return True

    return Recording(None, None, "call-room")


async def test_both_sides_are_in_the_real_recording_and_a_cut_off_reply_stops():
    session = fake_session(tone(440, 50))  # the person: one second
    sent = []
    uploads = []

    class Capture(FakeSession):
        def post(self, url, data, headers):
            uploads.append((data, headers))
            return super().post(url, data, headers)

    recorder = CallRecorder(
        session=session,
        room="call-room",
        reporter=reporter(sent),
        upload_url=UPLOAD_URL,
        secret=SECRET,
        conversation_started_at=time.time() - 3.0,
        session_factory=Capture,
    )
    FakeSession.status, FakeSession.error, FakeSession.sent = 201, None, []
    assert "Recording has started" in await recorder.consent(True)
    await recorder.flush()
    assert sent[0][0] == "recording" and sent[0][1]["state"] == "started"
    assert 2.9 <= sent[0][1]["offset"] <= 4.0

    # The person speaks for one second (the session pulls their frames).
    async for _ in session.input.audio:
        pass
    # Then the AI employee plays one second, cut off halfway.
    out = session.output.audio
    out.on_playback_started(created_at=time.time())
    for frame in tone(880, 50):
        await out.capture_frame(frame)
    out.flush()
    await asyncio.sleep(0.5)
    out.on_playback_finished(playback_position=0.5, interrupted=True)

    assert await recorder.finish() is True
    data, headers = uploads[0]
    assert data.startswith(b"OggS")
    # Signed exactly as Django checks it.
    assert headers["X-Call-Signature"] == sign_upload(
        SECRET,
        headers["X-Call-Timestamp"],
        "call-room",
        headers["X-Recording-Duration"],
        headers["X-Recording-Offset"],
        data,
    )

    left, right = _decode(data)
    person = _active_seconds(left)
    ai = _active_seconds(right)
    assert 0.8 <= person <= 1.3, person
    assert 0.3 <= ai <= 0.7, f"the cut-off reply should stop at 0.5 s, not {ai}"
    # Each side is on its own channel.
    assert _dominant_hz(left) == pytest.approx(440, abs=40)
    assert _dominant_hz(right) == pytest.approx(880, abs=40)


def _decode(data):
    import io as stdio

    with av.open(stdio.BytesIO(data)) as container:
        stream = container.streams.audio[0]
        assert stream.channels == 2
        chunks = [frame.to_ndarray() for frame in container.decode(stream)]
    pcm = (
        np.concatenate(chunks, axis=1)
        if chunks[0].ndim == 2 and chunks[0].shape[0] == 2
        else None
    )
    if pcm is None:  # interleaved packed samples
        flat = np.concatenate([c.reshape(-1) for c in chunks])
        pcm = flat.reshape(-1, 2).T
    return pcm[0].astype(np.float64), pcm[1].astype(np.float64)


def _active_seconds(channel, rate=RATE):
    window = rate // 50
    blocks = len(channel) // window
    energy = np.sqrt(
        np.mean(channel[: blocks * window].reshape(blocks, window) ** 2, axis=1)
    )
    loud = energy > energy.max() * 0.2
    return loud.sum() * window / rate


def _dominant_hz(channel, rate=RATE):
    spectrum = np.abs(np.fft.rfft(channel))
    return np.fft.rfftfreq(len(channel), 1 / rate)[spectrum.argmax()]


# --- consent ---------------------------------------------------------------------------


def make_recorder(sent, **kwargs):
    started = []

    class FakeRecorder:
        output_path = None

        def __init__(self, agent_session):
            self.closed = False

        def record_input(self, audio):
            return audio

        def record_output(self, audio):
            return audio

        async def start(self, *, output_path):
            self.output_path = output_path
            output_path.write_bytes(b"OggS-fake-audio")
            started.append(output_path)

        async def aclose(self):
            self.closed = True

    defaults = {
        "session": fake_session([]),
        "room": "call-room",
        "reporter": reporter(sent),
        "upload_url": UPLOAD_URL,
        "secret": SECRET,
        "conversation_started_at": time.time(),
        "session_factory": FakeSession,
        "recorder_factory": FakeRecorder,
    }
    defaults.update(kwargs)
    return CallRecorder(**defaults), started


async def test_no_or_no_answer_records_nothing():
    sent = []
    recorder, started = make_recorder(sent)
    assert "Nothing is recorded" in await recorder.consent(False)
    await recorder.flush()
    assert started == [] and sent == [("recording", {"state": "declined"})]
    assert await recorder.finish() is False
    # No answer at all: the call ends and nothing was started or sent.
    quiet, started = make_recorder([])
    assert await quiet.finish() is False and started == []


async def test_a_change_of_mind_deletes_what_was_recorded():
    sent = []
    recorder, started = make_recorder(sent)
    await recorder.consent(True)
    path = started[0]
    assert path.exists()
    await recorder.consent(False)
    await recorder.flush()
    assert not path.exists() and not path.parent.exists()
    assert sent[-1] == ("recording", {"state": "declined"})
    FakeSession.sent = []
    assert await recorder.finish() is False and FakeSession.sent == []


async def test_recording_cannot_start_without_audio_or_an_upload_address():
    sent = []
    silent = SimpleNamespace(
        input=SimpleNamespace(audio=None), output=SimpleNamespace(audio=None)
    )
    recorder, _ = make_recorder(sent, session=silent)
    assert "could not start" in await recorder.consent(True)
    await recorder.flush()
    assert sent == [
        ("recording", {"state": "failed", "reason": "the call has no audio to record"})
    ]
    sent.clear()
    nowhere, _ = make_recorder(sent, upload_url=None)
    await nowhere.consent(True)
    await nowhere.flush()
    assert sent[0][1]["state"] == "failed"


# --- delivery --------------------------------------------------------------------------


@pytest.fixture
def no_wait(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(recording.asyncio, "sleep", instant)


async def test_the_upload_is_retried_and_the_local_copy_always_removed(no_wait):
    attempts = []

    class Flaky(FakeSession):
        def post(self, url, data, headers):
            attempts.append(data)
            if len(attempts) < 2:
                raise ConnectionError("django restarting")
            return super().post(url, data, headers)

    sent = []
    FakeSession.status, FakeSession.error = 201, None
    recorder, started = make_recorder(sent, session_factory=Flaky)
    await recorder.consent(True)
    assert await recorder.finish() is True
    assert len(attempts) == 2 and not started[0].parent.exists()


async def test_a_refused_upload_is_not_retried_and_is_reported(no_wait):
    sent = []
    FakeSession.status, FakeSession.error, FakeSession.sent = 409, None, []
    recorder, started = make_recorder(sent)
    await recorder.consent(True)
    assert await recorder.finish() is False
    assert len(FakeSession.sent) == 1 and not started[0].parent.exists()
    assert sent[-1] == (
        "recording",
        {"state": "failed", "reason": "the upload to Django did not succeed"},
    )
    FakeSession.status = 204


# --- the question --------------------------------------------------------------------


def test_the_question_is_asked_only_when_django_allows_recording():
    allowed = parse_profile(
        json.dumps(
            {
                "agent_profile": {
                    "purpose": "Collect feedback.",
                    "greeting": "Hello, this is Ravi.",
                    "recording": {"notice": "May I record this call?"},
                }
            }
        )
    )
    plain = parse_profile(
        json.dumps({"agent_profile": {"purpose": "x", "greeting": "Hi."}})
    )
    assert allowed.recording_notice == "May I record this call?"
    assert "recording_consent" in profile_instructions(allowed)
    assert (
        plain.recording_notice == ""
        and "recording_consent" not in profile_instructions(plain)
    )

    said = []
    session = SimpleNamespace(say=lambda text, **kwargs: said.append(text))
    greet(session, make_settings(), profile=allowed)
    greet(session, make_settings(), profile=plain)
    assert said == ["Hello, this is Ravi. May I record this call?", "Hi."]


async def test_the_sdk_recorder_is_the_one_used():
    recorder, _ = make_recorder([], recorder_factory=None)
    assert isinstance(recorder._recorder_factory(SimpleNamespace()), RecorderIO)


async def test_the_caller_never_waits_for_django_to_hear_about_consent():
    gate = asyncio.Event()
    sent = []

    class SlowReporter:
        async def send(self, event, **fields):
            await gate.wait()
            sent.append((event, fields))

    recorder, _ = make_recorder(sent)
    recorder._reporter = SlowReporter()
    reply = await asyncio.wait_for(recorder.consent(True), timeout=1)
    assert "Recording has started" in reply and sent == []
    await recorder.consent(False)
    gate.set()
    await recorder.flush()
    # Sent in the order they happened, even though both were held up.
    assert [fields["state"] for _, fields in sent] == ["started", "declined"]
