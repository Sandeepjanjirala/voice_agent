"""Read encrypted numbers for records already scoped to an authorised user.

Display access never bypasses the view's organisation and location permissions.
Storage, logs and audit events continue to use encrypted or redacted values.
"""

from . import crypto

UNAVAILABLE = "Number unavailable"


def decrypted_number(token: str) -> str:
    """Return a stored number, or an empty value when it cannot be recovered."""
    if not token:
        return ""
    try:
        return crypto.decrypt(token)
    except crypto.CryptoError:
        return ""
