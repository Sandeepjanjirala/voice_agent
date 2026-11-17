"""Keys for stored phone numbers: create them, check them, rotate them.

    manage.py contactkeys generate   print new values for .env.local
    manage.py contactkeys status     are the keys set, and is everything on the newest?
    manage.py contactkeys rotate     re-encrypt every number with the newest key
    manage.py contactkeys rehash     recompute match values after changing the lookup key
    manage.py contactkeys fingerprint  short fingerprints, to check a backup copy

No command prints a phone number or an existing key.
"""

import hashlib
import secrets

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.crm import crypto
from apps.crm.models import Contact, OptOut


def _stored():
    """Every row that holds an encrypted number."""
    yield from Contact.objects.exclude(phone_encrypted="").iterator()
    yield from OptOut.objects.iterator()


class Command(BaseCommand):
    help = "Create, check and rotate the keys that protect stored phone numbers."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "action",
            choices=("generate", "status", "rotate", "rehash", "fingerprint"),
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Report without changing anything."
        )

    def handle(self, *args, action: str, dry_run: bool, **options) -> None:
        if action == "generate":
            self.stdout.write(
                "Add these two lines to .env.local (never to Git), then restart "
                "Django.\nKeep a copy somewhere safe: without them the stored "
                "numbers cannot be read.\n"
            )
            self.stdout.write(f"CONTACT_ENCRYPTION_KEYS={crypto.new_encryption_key()}")
            self.stdout.write(f"CONTACT_LOOKUP_KEY={secrets.token_urlsafe(48)}")
            return
        problem = crypto.problem()
        if problem:
            raise CommandError(problem)
        getattr(self, f"_{action}")(dry_run)

    def _fingerprint(self, dry_run: bool) -> None:
        """Fingerprints that identify each key without revealing it. Run the
        same command against a backup copy: equal fingerprints, same keys."""
        from django.conf import settings

        def short(value: str) -> str:
            return hashlib.sha256(value.encode()).hexdigest()[:16]

        for index, key in enumerate(settings.CONTACT_ENCRYPTION_KEYS, start=1):
            role = "encrypts and decrypts" if index == 1 else "decrypts only"
            self.stdout.write(f"Encryption key {index} ({role}): {short(key)}")
        self.stdout.write(f"Lookup key: {short(settings.CONTACT_LOOKUP_KEY)}")

    def _status(self, dry_run: bool) -> None:
        total = old = unreadable = 0
        for row in _stored():
            total += 1
            if crypto.uses_current_key(row.phone_encrypted):
                continue
            try:
                crypto.decrypt(row.phone_encrypted)
            except crypto.CryptoError:
                unreadable += 1
            else:
                old += 1
        self.stdout.write(f"Stored numbers: {total}")
        self.stdout.write(f"Encrypted with an older key (run rotate): {old}")
        if unreadable:
            raise CommandError(
                f"{unreadable} stored numbers cannot be read with the configured "
                "keys. An old key is missing from CONTACT_ENCRYPTION_KEYS."
            )
        self.stdout.write("Every stored number can be read.")

    def _rotate(self, dry_run: bool) -> None:
        changed = 0
        with transaction.atomic():
            for row in _stored():
                if crypto.uses_current_key(row.phone_encrypted):
                    continue
                try:
                    token = crypto.rotate(row.phone_encrypted)
                except crypto.CryptoError as error:
                    # Nothing is half-rotated: the transaction is rolled back.
                    raise CommandError(f"{error} Nothing was changed.") from None
                changed += 1
                if not dry_run:
                    type(row).objects.filter(pk=row.pk).update(phone_encrypted=token)
        verb = "would be" if dry_run else "were"
        self.stdout.write(f"{changed} stored numbers {verb} re-encrypted.")
        if not dry_run and changed:
            self.stdout.write(
                "Once backups made before today are no longer needed, remove the "
                "old key from CONTACT_ENCRYPTION_KEYS."
            )

    def _rehash(self, dry_run: bool) -> None:
        changed = 0
        with transaction.atomic():
            seen: dict[tuple, int] = {}
            updates = []
            for row in _stored():
                try:
                    lookup = crypto.lookup(crypto.decrypt(row.phone_encrypted))
                except crypto.CryptoError as error:
                    raise CommandError(f"{error} Nothing was changed.") from None
                key = (type(row).__name__, row.organisation_id, lookup)
                if key in seen:
                    raise CommandError(
                        "Two stored rows hold the same number in one organisation. "
                        "Nothing was changed."
                    )
                seen[key] = row.pk
                if lookup != row.phone_lookup:
                    updates.append((row, lookup))
            changed = len(updates)
            if not dry_run:
                # Two passes, so a unique constraint cannot trip half-way.
                for row, _ in updates:
                    type(row).objects.filter(pk=row.pk).update(
                        phone_lookup=f"rehash:{secrets.token_hex(16)}"
                    )
                for row, lookup in updates:
                    type(row).objects.filter(pk=row.pk).update(phone_lookup=lookup)
        verb = "would be" if dry_run else "were"
        self.stdout.write(f"{changed} match values {verb} recomputed.")
