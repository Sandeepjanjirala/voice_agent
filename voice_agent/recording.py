"""Records a phone call, both sides, after the person agrees.

Only for a call whose profile carries a recording question: Django decides
which calls may be recorded (apps/calling/recordings.py). The question is said
with the opening line; the agent passes the answer to `recording_consent`.

- A clear yes: LiveKit's session recorder is attached to the call's audio. The
  person is on the left channel (as their audio arrived) and the AI employee on
  the right (as it was played to them, so an interrupted reply stops where the
  person cut it off). It is the real call audio, never regenerated.
- No, or no clear answer: nothing is captured, and Django is told so.
- A change of mind during the call: recording stops and the file is deleted.

When the call ends the file is closed and uploaded to Django, signed with
CALL_EVENTS_SECRET; the local copy is deleted whether or not the upload worked.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import aiohttp
from livekit.agents import function_tool
from livekit.agents.voice.recorder_io import RecorderIO

from .call_reporting import SIGNATURE_HEADER, TIMESTAMP_HEADER, CallReporter

logger = logging.getLogger("voice_agent")

# Speech needs little: 32 kbit/s stereo Opus is about 240 kB a minute.
BIT_RATE = 32_000
UPLOAD_ATTEMPTS = 3
UPLOAD_TIMEOUT_SECONDS = 60.0
ROOM_HEADER = "X-Call-Room"
DURATION_HEADER = "X-Recording-Duration"
OFFSET_HEADER = "X-Recording-Offset"


def sign_upload(
    secret: str, timestamp: str, room: str, duration: str, offset: str, data: bytes
) -> str:
    """The signature Django checks (apps/telephony/views.py call_recording)."""
    message = f"{timestamp}.{room}.{duration}.{offset}.".encode() + data
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class CallRecorder:
    """One call's recording. States: waiting, recording, declined, failed, done."""

    def __init__(
        self,
        *,
        session: Any,
        room: str,
        reporter: CallReporter,
        upload_url: str | None,
        secret: str | None,
        conversation_started_at: float,
        session_factory=aiohttp.ClientSession,
        recorder_factory=None,
    ) -> None:
        self._session = session
        self._room = room
        self._reporter = reporter
        self._upload_url = upload_url
        self._secret = secret
        self._started_at = conversation_started_at
        self._session_factory = session_factory
        self._recorder_factory = recorder_factory or (
            lambda agent_session: RecorderIO(
                agent_session=agent_session, bit_rate=BIT_RATE
            )
        )
        self._recorder: Any = None
        self._directory: Path | None = None
        self._recording_from: float | None = None
        # Recording events go to Django in the background, one after another,
        # so the caller never waits in silence while Django is slow.
        self._last_report: asyncio.Task | None = None
        self.state = "waiting"

    @property
    def can_upload(self) -> bool:
        return bool(self._upload_url and self._secret)

    def tools(self) -> list[Any]:
        recorder = self

        @function_tool
        async def recording_consent(agreed: bool) -> str:
            """Call once with the person's answer to the recording question in
            your opening line: agreed=true only for a clear yes, false for no,
            an unclear answer or no answer. Call again with false if they later
            ask not to be recorded."""
            return await recorder.consent(agreed)

        return [recording_consent]

    async def consent(self, agreed: bool) -> str:
        if agreed and self.state == "waiting":
            await self._start()
            if self.state == "recording":
                return "Recording has started. Continue the call."
            return "Recording could not start. Continue the call without it."
        if not agreed and self.state in ("waiting", "recording"):
            await self._discard()
            self.state = "declined"
            self._report(state="declined")
            return "Nothing is recorded. Continue the call."
        return "Already noted. Continue the call."

    async def _start(self) -> None:
        if not self.can_upload:
            await self._fail("the recording upload address is not configured")
            return
        if self._session.input.audio is None or self._session.output.audio is None:
            await self._fail("the call has no audio to record")
            return
        try:
            self._directory = Path(tempfile.mkdtemp(prefix="call-recording-"))
            recorder = self._recorder_factory(self._session)
            self._session.input.audio = recorder.record_input(self._session.input.audio)
            self._session.output.audio = recorder.record_output(
                self._session.output.audio
            )
            await recorder.start(output_path=self._directory / "call.ogg")
        except Exception as error:
            await self._fail(type(error).__name__)
            return
        self._recorder = recorder
        self._recording_from = time.time()
        self.state = "recording"
        self._report(
            state="started",
            offset=round(max(self._recording_from - self._started_at, 0.0), 2),
        )

    async def _fail(self, reason: str) -> None:
        logger.warning("call not recorded", extra={"reason": reason})
        self._remove_files()
        self.state = "failed"
        self._report(state="failed", reason=reason)

    def _report(self, **fields: Any) -> None:
        """Send a recording event without waiting for it, after the one before."""
        previous = self._last_report

        async def send() -> None:
            if previous is not None:
                await asyncio.gather(previous, return_exceptions=True)
            await self._reporter.send("recording", **fields)

        self._last_report = asyncio.create_task(send())

    async def flush(self) -> None:
        """Wait for the recording events still on their way."""
        if self._last_report is not None:
            await asyncio.gather(self._last_report, return_exceptions=True)

    async def _close(self) -> Path | None:
        recorder, self._recorder = self._recorder, None
        if recorder is None:
            return None
        try:
            await recorder.aclose()
        except Exception as error:
            logger.warning(
                "recorder did not close", extra={"error_type": type(error).__name__}
            )
        path = recorder.output_path
        return Path(path) if path else None

    async def _discard(self) -> None:
        await self._close()
        self._remove_files()

    def _remove_files(self) -> None:
        if self._directory is not None:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = None

    async def finish(self) -> bool:
        """At the end of the call: close the file and upload it. Returns True
        when Django has the recording."""
        await self.flush()
        if self.state != "recording":
            return False
        path = await self._close()
        try:
            data = path.read_bytes() if path is not None and path.exists() else b""
        except OSError:
            data = b""
        finally:
            self._remove_files()
        if not data:
            self.state = "failed"
            await self._reporter.send(
                "recording", state="failed", reason="the recording file was empty"
            )
            return False
        duration = round(
            max(time.time() - (self._recording_from or time.time()), 0.0), 2
        )
        offset = round(
            max((self._recording_from or self._started_at) - self._started_at, 0.0), 2
        )
        delivered = await self._upload(data, f"{duration:.2f}", f"{offset:.2f}")
        self.state = "done" if delivered else "failed"
        if not delivered:
            await self._reporter.send(
                "recording",
                state="failed",
                reason="the upload to Django did not succeed",
            )
        return delivered

    async def _upload(self, data: bytes, duration: str, offset: str) -> bool:
        for attempt in range(1, UPLOAD_ATTEMPTS + 1):
            timestamp = str(int(time.time()))
            headers = {
                "Content-Type": "audio/ogg",
                TIMESTAMP_HEADER: timestamp,
                ROOM_HEADER: self._room,
                DURATION_HEADER: duration,
                OFFSET_HEADER: offset,
                SIGNATURE_HEADER: sign_upload(
                    self._secret, timestamp, self._room, duration, offset, data
                ),
            }
            status = None
            try:
                timeout = aiohttp.ClientTimeout(total=UPLOAD_TIMEOUT_SECONDS)
                async with (
                    self._session_factory(timeout=timeout) as http,
                    http.post(self._upload_url, data=data, headers=headers) as response,
                ):
                    status = response.status
            except Exception as error:
                logger.warning(
                    "recording upload failed",
                    extra={"error_type": type(error).__name__},
                )
            if status is not None and status < 300:
                logger.info("call recording uploaded", extra={"bytes": len(data)})
                return True
            # A refusal (no consent on record, too large, bad signature) will not change.
            if status is not None and status < 500:
                logger.warning(
                    "recording refused by Django", extra={"http_status": status}
                )
                return False
            if attempt < UPLOAD_ATTEMPTS:
                await asyncio.sleep(1.0 * attempt)
        return False


def recorder_for(
    ctx: Any,
    settings: Any,
    profile: Any,
    session: Any,
    reporter: CallReporter,
    *,
    started_at: float,
) -> CallRecorder | None:
    """A recorder for this call if Django allows recording it, else None."""
    if profile is None or not profile.recording_notice:
        return None
    return CallRecorder(
        session=session,
        room=ctx.room.name,
        reporter=reporter,
        upload_url=settings.call_recording_url,
        secret=settings.call_events_secret,
        conversation_started_at=started_at,
    )
