"""Set up and check call-recording storage.

manage.py recordings key      print a new encryption key for RECORDING_ENCRYPTION_KEYS
manage.py recordings status   show the storage settings (never a secret)
manage.py recordings check    store, read back and delete a small test file
                              (not call audio) with the configured storage
"""

import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.calling import recordings


class Command(BaseCommand):
    help = "Set up and check call-recording storage."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("key", "status", "check"))

    def handle(self, *args, action, **options):
        if action == "key":
            self.stdout.write(f"RECORDING_ENCRYPTION_KEYS={recordings.new_key()}")
            self.stdout.write(
                "Add this line to .env.local (never to Git) and restart Django. "
                "Keep a copy somewhere safe: without it no recording can be played."
            )
            return
        if action == "status":
            self._status()
            return
        self._check()

    def _status(self):
        keys = len(settings.RECORDING_ENCRYPTION_KEYS)
        self.stdout.write(f"Storage: {settings.RECORDING_STORAGE}")
        if settings.RECORDING_STORAGE == "s3":
            self.stdout.write(f"Bucket: {settings.RECORDING_S3_BUCKET or '(not set)'}")
            self.stdout.write(f"Region: {settings.RECORDING_S3_REGION}")
            if settings.RECORDING_S3_ENDPOINT:
                self.stdout.write(f"Endpoint: {settings.RECORDING_S3_ENDPOINT}")
            self.stdout.write(
                "Server-side encryption: "
                + (
                    "KMS key"
                    if settings.RECORDING_S3_KMS_KEY_ID
                    else "S3-managed (AES256)"
                )
            )
            self.stdout.write(
                "Credentials: "
                + (
                    "set"
                    if settings.RECORDING_S3_ACCESS_KEY_ID
                    else "from the environment or instance role"
                )
            )
        else:
            self.stdout.write(f"Folder: {settings.RECORDING_LOCAL_DIR}")
        self.stdout.write(
            f"Encryption keys: {keys} set" if keys else "Encryption keys: NOT SET"
        )
        if not settings.CALL_EVENTS_SECRET:
            self.stdout.write(
                "CALL_EVENTS_SECRET is not set: the worker cannot upload recordings."
            )

    def _check(self):
        key = f"_check/{uuid.uuid4()}.enc"
        sample = b"OggS recording storage check " + uuid.uuid4().bytes
        try:
            cipher = recordings._cipher()
            store = recordings.storage()
            store.put(key, cipher.encrypt(sample))
            back = cipher.decrypt(store.get(key))
            store.delete(key)
        except recordings.RecordingError as error:
            raise CommandError(f"Recording storage does not work: {error}.") from None
        if back != sample:
            raise CommandError("Recording storage returned different bytes.")
        self.stdout.write(
            f"Recording storage works ({settings.RECORDING_STORAGE}): a test file was "
            "stored encrypted, read back and deleted."
        )
