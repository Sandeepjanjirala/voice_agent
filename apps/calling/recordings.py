"""Call recordings: which calls may be recorded, where the audio is kept, and
who may hear it.

- **Which calls.** The organisation decides (Privacy & settings): never, approved
  test calls only, or any call. Django marks each call when it is planned; the
  voice worker records only a call Django marked, and only after the person
  agreed on the call. Django refuses audio for any other call.
- **The audio.** The voice worker records both sides (the person on the left
  channel, the AI employee on the right) as an Ogg Opus file and uploads it
  here, signed, when the call ends. Django encrypts it (Fernet, keys in
  RECORDING_ENCRYPTION_KEYS) before storing it in a private folder or a private
  S3 bucket, which adds its own server-side encryption.
- **Who hears it.** Only people who may see the call, with the
  "calls.recordings" permission (downloading needs "calls.recordings_download"),
  through a link that works for ten minutes, for that person only. The file is
  streamed by Django; no storage URL or credential reaches the browser, and
  every access is logged.
- **For how long.** RECORDING_DAYS of the organisation's retention policy,
  independent of transcripts; deleting a call deletes its recording.
"""

import hashlib
import logging
import os
import tempfile
from datetime import timedelta
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from .models import CallRecording

logger = logging.getLogger(__name__)

Status = CallRecording.Status
# The largest file accepted from the worker: well over an hour of speech at
# the worker's 32 kbit/s.
MAX_BYTES = 50 * 1024 * 1024
OGG_MAGIC = b"OggS"
# A recording that has not arrived this long after the call ended has failed.
ARRIVAL_GRACE = timedelta(minutes=15)
LINK_SECONDS = 600
TOKEN_SALT = "call-recording-access"
# The voice worker's channels (voice_agent/recording.py).
CHANNELS = {"left": "The person on the call", "right": "The AI employee"}


class RecordingError(Exception):
    """Something the caller of this module should show or report."""


# --- which calls may be recorded ------------------------------------------------------


def allowed_for(organisation, *, test_number: bool) -> tuple[bool, str]:
    """Whether this organisation records this call, and if not, why not."""
    from apps.tenancy.models import Organisation

    mode = organisation.recording if organisation is not None else "off"
    if mode == Organisation.Recording.OFF:
        return False, "Call recording is switched off for this organisation."
    if mode == Organisation.Recording.TEST_CALLS and not test_number:
        return False, "Only approved test calls are recorded for this organisation."
    return True, ""


def mark_call(
    payload: dict, snapshot: dict, organisation, *, test_number: bool
) -> bool:
    """Tell the worker whether it may ask to record this call. `payload` is the
    profile the worker receives; `snapshot` is kept with the call record."""
    allowed, reason = allowed_for(organisation, test_number=test_number)
    snapshot["recording"] = {"allowed": allowed, "reason": reason}
    if allowed:
        payload["recording"] = {"notice": organisation.recording_notice}
    else:
        payload.pop("recording", None)
    return allowed


def _allowed(record) -> bool:
    return bool((record.context_snapshot or {}).get("recording", {}).get("allowed"))


# --- events from the voice worker ----------------------------------------------------


def note_event(record, payload: dict) -> CallRecording | None:
    """A "recording" event: the person agreed and recording started, they
    declined, or the worker could not record. Safe to receive twice."""
    state = payload.get("state")
    if state not in ("started", "declined", "failed") or record.organisation is None:
        return None
    if not _allowed(record):
        logger.warning("recording event for a call that may not be recorded")
        return None
    offset = _seconds(payload.get("offset"))
    with transaction.atomic():
        recording, created = CallRecording.objects.select_for_update().get_or_create(
            call=record,
            defaults={"organisation": record.organisation, "status": Status.RECORDING},
        )
        if not created and recording.status in (Status.AVAILABLE, Status.DELETED):
            return recording
        if state == "started":
            recording.status = Status.RECORDING
            recording.consented_at = recording.consented_at or timezone.now()
            recording.offset_seconds = offset or 0
            recording.reason = ""
        elif state == "declined":
            recording.status = Status.NOT_RECORDED
            recording.reason = "The person did not agree to the call being recorded, so nothing was recorded."
        else:
            recording.status = Status.FAILED
            detail = " ".join(str(payload.get("reason") or "").split())[:200]
            recording.reason = f"The voice worker could not record the call{': ' + detail if detail else '.'}"
        recording.save()
    return recording


def _seconds(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(float(value), 2) if 0 <= value <= 7200 else None


def receive(record, data: bytes, *, duration, offset) -> CallRecording:
    """The audio the worker uploaded for a call it was allowed to record and the
    person agreed to. Raises RecordingError (with an HTTP-like status)."""
    if record.organisation is None or not _allowed(record):
        raise RecordingError("This call may not be recorded.", 403)
    recording = CallRecording.objects.filter(call=record).first()
    if recording is None or recording.consented_at is None:
        raise RecordingError("No consent was recorded for this call.", 409)
    if recording.status == Status.DELETED:
        raise RecordingError("This recording was deleted.", 410)
    digest = hashlib.sha256(data).hexdigest()
    if recording.status == Status.AVAILABLE:
        if recording.sha256 == digest:
            return recording  # the worker retried a delivered upload
        raise RecordingError("This call already has a recording.", 409)
    if not data.startswith(OGG_MAGIC):
        raise RecordingError("The upload is not an Ogg audio file.", 400)
    if len(data) > MAX_BYTES:
        raise RecordingError("The recording is too large.", 413)
    recording.status = Status.PROCESSING
    recording.save(update_fields=["status", "updated_at"])
    try:
        key = store(recording, data)
    except RecordingError as error:
        recording.status = Status.FAILED
        recording.reason = f"The recording could not be stored: {error}"[:300]
        recording.save(update_fields=["status", "reason", "updated_at"])
        raise RecordingError(str(error), 503) from None
    recording.status = Status.AVAILABLE
    recording.reason = ""
    recording.object_key = key
    recording.storage = settings.RECORDING_STORAGE
    recording.size_bytes = len(data)
    recording.sha256 = digest
    recording.duration_seconds = _seconds(duration)
    if offset is not None:
        recording.offset_seconds = _seconds(offset) or 0
    recording.stored_at = timezone.now()
    recording.save()
    from apps.tenancy import audit

    audit.record_system(record.organisation, "recording.stored", call=str(record.pk))
    return recording


def settle(recording: CallRecording, now=None) -> CallRecording:
    """Fail a recording that never arrived, so the page says so."""
    now = now or timezone.now()
    call = recording.call
    if (
        recording.status in (Status.RECORDING, Status.PROCESSING)
        and not call.is_active
        and (call.ended_at or call.updated_at) + ARRIVAL_GRACE < now
    ):
        recording.status = Status.FAILED
        recording.reason = (
            "The recording did not arrive from the voice worker after the call "
            "ended (the worker may have stopped, or could not reach Django)."
        )
        recording.save(update_fields=["status", "reason", "updated_at"])
    return recording


# --- storage ----------------------------------------------------------------------------


def _cipher() -> MultiFernet:
    keys = settings.RECORDING_ENCRYPTION_KEYS
    if not keys:
        raise RecordingError("RECORDING_ENCRYPTION_KEYS is not set")
    try:
        return MultiFernet([Fernet(key.encode()) for key in keys])
    except ValueError:
        raise RecordingError("RECORDING_ENCRYPTION_KEYS holds an invalid key") from None


def new_key() -> str:
    return Fernet.generate_key().decode()


class LocalStorage:
    """A private folder on this server. Files are written whole, then renamed,
    so a half-written file is never read."""

    def __init__(self, base: Path) -> None:
        self.base = Path(base).resolve()

    def _path(self, key: str) -> Path:
        path = (self.base / key).resolve()
        if self.base not in path.parents:
            raise RecordingError("invalid recording path")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".part")
            with os.fdopen(handle, "wb") as file:
                file.write(data)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except OSError as error:
            raise RecordingError(
                f"local storage failed ({type(error).__name__})"
            ) from None

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except OSError:
            raise RecordingError("the recording file is missing") from None

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as error:
            raise RecordingError(
                f"local delete failed ({type(error).__name__})"
            ) from None


class S3Storage:
    """A private S3-compatible bucket, with server-side encryption on top of
    our own. The bucket must block all public access."""

    def __init__(self, client=None) -> None:
        if not settings.RECORDING_S3_BUCKET:
            raise RecordingError("RECORDING_S3_BUCKET is not set")
        self.bucket = settings.RECORDING_S3_BUCKET
        self.client = client or self._client()

    @staticmethod
    def _client():
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            region_name=settings.RECORDING_S3_REGION,
            endpoint_url=settings.RECORDING_S3_ENDPOINT or None,
            aws_access_key_id=settings.RECORDING_S3_ACCESS_KEY_ID or None,
            aws_secret_access_key=settings.RECORDING_S3_SECRET_ACCESS_KEY or None,
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )

    def put(self, key: str, data: bytes) -> None:
        extra = (
            {
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": settings.RECORDING_S3_KMS_KEY_ID,
            }
            if settings.RECORDING_S3_KMS_KEY_ID
            else {"ServerSideEncryption": "AES256"}
        )
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType="application/octet-stream",
                **extra,
            )
        except Exception as error:
            raise RecordingError(f"S3 upload failed ({type(error).__name__})") from None

    def get(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as error:
            raise RecordingError(
                f"S3 download failed ({type(error).__name__})"
            ) from None

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            raise RecordingError(f"S3 delete failed ({type(error).__name__})") from None


# Tests replace this with a fake; nothing else should.
_storage_override = None


def storage(name: str | None = None):
    if _storage_override is not None:
        return _storage_override
    name = name or settings.RECORDING_STORAGE
    if name == "s3":
        return S3Storage()
    return LocalStorage(settings.RECORDING_LOCAL_DIR)


def store(recording: CallRecording, data: bytes) -> str:
    """Encrypt and store; returns the object key."""
    key = f"{recording.organisation_id}/{recording.call_id}.ogg.enc"
    storage().put(key, _cipher().encrypt(data))
    return key


def read(recording: CallRecording) -> bytes:
    """The decrypted audio of an available recording."""
    if not recording.playable:
        raise RecordingError("The recording is not available.")
    encrypted = storage(recording.storage or None).get(recording.object_key)
    try:
        return _cipher().decrypt(encrypted)
    except InvalidToken:
        raise RecordingError(
            "the recording could not be decrypted with the current keys"
        ) from None


def delete(recording: CallRecording, reason: str) -> CallRecording:
    """Remove the stored file and keep a row that says when and why."""
    if recording.object_key:
        storage(recording.storage or None).delete(recording.object_key)
    recording.status = Status.DELETED
    recording.reason = reason[:300]
    recording.object_key = ""
    recording.deleted_at = timezone.now()
    recording.save()
    return recording


def discard_file(recording: CallRecording) -> None:
    """For a recording row that is itself being deleted (with its call)."""
    if recording.object_key:
        try:
            storage(recording.storage or None).delete(recording.object_key)
        except RecordingError as error:
            logger.error("recording file not deleted", extra={"error": str(error)})


# --- access ---------------------------------------------------------------------------


def link_token(recording: CallRecording, user, *, download: bool = False) -> str:
    """A token for one person to fetch one recording, valid for LINK_SECONDS."""
    return signing.dumps(
        {"r": str(recording.pk), "u": user.pk, "d": bool(download)}, salt=TOKEN_SALT
    )


def token_valid(token: str, recording: CallRecording, user, *, download: bool) -> bool:
    try:
        data = signing.loads(token, salt=TOKEN_SALT, max_age=LINK_SECONDS)
    except signing.BadSignature:
        return False
    return (
        data.get("r") == str(recording.pk)
        and data.get("u") == user.pk
        and bool(data.get("d")) == download
    )
