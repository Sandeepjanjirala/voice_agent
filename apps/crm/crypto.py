"""Encryption of stored phone numbers, and the keyed hash used to match them.

Two secrets, both from the environment and never from the database:

CONTACT_ENCRYPTION_KEYS  Fernet keys (AES-128-CBC with HMAC-SHA256, so a
    changed or truncated value is rejected, not decrypted to nonsense). The
    first key encrypts; every key can decrypt. To rotate: put a new key first,
    run `manage.py contactkeys rotate`, then remove the old key.

CONTACT_LOOKUP_KEY  Key for HMAC-SHA256 of the normalised number. The result
    is what duplicate detection and the do-not-call list compare, so matching
    never needs a decrypted number, and the stored value cannot be reversed or
    recomputed by someone who only has the database.

Decrypting is scoped to permitted calling and authorised dashboard display.
"""

import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings

MIN_LOOKUP_KEY = 32


class CryptoError(Exception):
    """Keys are missing or wrong, or a stored value could not be read."""


def _fernets() -> list[Fernet]:
    keys = settings.CONTACT_ENCRYPTION_KEYS
    if not keys:
        raise CryptoError("CONTACT_ENCRYPTION_KEYS is not set.")
    try:
        return [Fernet(key.encode()) for key in keys]
    except ValueError:
        raise CryptoError(
            "CONTACT_ENCRYPTION_KEYS holds a value that is not a Fernet key."
        ) from None


def _lookup_key() -> bytes:
    key = settings.CONTACT_LOOKUP_KEY
    if len(key) < MIN_LOOKUP_KEY:
        raise CryptoError(
            f"CONTACT_LOOKUP_KEY must be at least {MIN_LOOKUP_KEY} characters."
        )
    return key.encode()


def problem() -> str:
    """Why contacts cannot be stored, or "" when the keys are usable."""
    try:
        _fernets()
        _lookup_key()
    except CryptoError as error:
        return str(error)
    return ""


def configured() -> bool:
    return not problem()


def encrypt(number: str) -> str:
    return _fernets()[0].encrypt(number.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return MultiFernet(_fernets()).decrypt(token.encode()).decode()
    except InvalidToken:
        raise CryptoError(
            "A stored number could not be decrypted with the configured keys."
        ) from None


def uses_current_key(token: str) -> bool:
    try:
        _fernets()[0].decrypt(token.encode())
    except InvalidToken:
        return False
    return True


def rotate(token: str) -> str:
    """The same number, encrypted again with the first (newest) key."""
    try:
        return MultiFernet(_fernets()).rotate(token.encode()).decode()
    except InvalidToken:
        raise CryptoError(
            "A stored number could not be decrypted with the configured keys."
        ) from None


def lookup(number: str) -> str:
    """Keyed hash of a normalised number: equal numbers, equal values."""
    return hmac.new(_lookup_key(), number.encode(), hashlib.sha256).hexdigest()


def new_encryption_key() -> str:
    return Fernet.generate_key().decode()
