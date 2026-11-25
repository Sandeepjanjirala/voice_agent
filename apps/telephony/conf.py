"""Typed, validated telephony settings read from the environment.

Malformed values raise TelephonyConfigError. Values that are well-formed but
missing, unsafe or unproven are not errors here: planning.build_plan() reports
them as blockers, so `manage.py telephony plan` can always explain what is left.
"""

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from .providers import CONFIRMATIONS, PROVIDERS

SIP_REGIONS = ("global", "aus", "canada", "eu", "india", "japan", "sa", "us")
TRANSPORTS = ("tls", "tcp")
AUTH_MODES = ("digest", "ip_allowlist")
MEDIA_ENCRYPTION = ("require", "allow", "off")

MIN_AUTH_PASSWORD_LENGTH = 12

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_SUBDOMAIN = re.compile(r"^[a-z0-9]{6,32}$")
_USERNAME = re.compile(r"^[A-Za-z0-9_.\-]{5,64}$")
_ROOM_PREFIX = re.compile(r"^[a-z0-9\-]{1,24}$")


class TelephonyConfigError(ValueError):
    """A telephony environment variable has a value that cannot be used."""


@dataclass(frozen=True)
class TelephonySettings:
    provider: str
    inbound_number: str
    sip_subdomain: str
    sip_region: str
    transport: str
    auth_mode: str
    auth_username: str
    # Never shown: excluded from repr and from every plan or status output.
    auth_password: str = field(repr=False)
    allowed_addresses: tuple[str, ...]
    media_encryption: str
    ringing_timeout_seconds: int
    max_call_duration_seconds: int
    hide_caller_number: bool
    room_prefix: str
    confirmed: frozenset[str]
    # Every number the inbound trunk accepts; inbound_number is the first.
    inbound_numbers: tuple[str, ...] = ()


def _choice(env: Mapping[str, str], name: str, default: str, allowed) -> str:
    value = env.get(name, "").strip().lower() or default
    if value not in allowed:
        raise TelephonyConfigError(f"{name} must be one of: {', '.join(allowed)}.")
    return value


def _bounded_int(
    env: Mapping[str, str], name: str, default: int, low: int, high: int
) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise TelephonyConfigError(f"{name} must be a whole number.") from None
    if not low <= value <= high:
        raise TelephonyConfigError(f"{name} must be between {low} and {high}.")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    raise TelephonyConfigError(f"{name} must be true or false.")


def _csv(env: Mapping[str, str], name: str) -> list[str]:
    return [item.strip() for item in env.get(name, "").split(",") if item.strip()]


def _addresses(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    parsed = []
    for item in _csv(env, name):
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError:
            raise TelephonyConfigError(
                f"{name} entries must be plain IP addresses or CIDR blocks."
            ) from None
        # A list that admits the whole internet is not an allow-list.
        if network.prefixlen < (16 if network.version == 4 else 32):
            raise TelephonyConfigError(
                f"{name} contains a range too wide to act as an allow-list."
            )
        parsed.append(str(network) if "/" in item else str(network.network_address))
    return tuple(parsed)


def load_telephony_settings(env: Mapping[str, str] | None = None) -> TelephonySettings:
    env = os.environ if env is None else env

    provider = env.get("TELEPHONY_PROVIDER", "").strip().lower()
    if provider and provider not in PROVIDERS:
        raise TelephonyConfigError(
            f"TELEPHONY_PROVIDER must be one of: {', '.join(sorted(PROVIDERS))}."
        )

    # One number, or several separated by commas: every business line the
    # inbound trunk accepts. Numbers routed on the dashboard are added by
    # `manage.py telephony` (apps/calling/inbound.trunk_numbers).
    numbers = tuple(
        dict.fromkeys(
            part.strip().replace(" ", "")
            for part in env.get("TELEPHONY_INBOUND_NUMBER", "").split(",")
            if part.strip()
        )
    )
    if any(not _E164.match(n) for n in numbers):
        raise TelephonyConfigError(
            "TELEPHONY_INBOUND_NUMBER must be in E.164 form, such as +91XXXXXXXXXX "
            "(several separated by commas)."
        )
    number = numbers[0] if numbers else ""

    subdomain = env.get("LIVEKIT_SIP_SUBDOMAIN", "").strip().lower()
    if subdomain and not _SUBDOMAIN.match(subdomain):
        raise TelephonyConfigError(
            "LIVEKIT_SIP_SUBDOMAIN is the LiveKit project ID without the p_ prefix: "
            "lowercase letters and digits only."
        )

    username = env.get("SIP_INBOUND_AUTH_USERNAME", "").strip()
    if username and not _USERNAME.match(username):
        raise TelephonyConfigError(
            "SIP_INBOUND_AUTH_USERNAME must be 5 to 64 letters, digits, '.', '_' or '-'."
        )

    password = env.get("SIP_INBOUND_AUTH_PASSWORD", "")
    if password and len(password) < MIN_AUTH_PASSWORD_LENGTH:
        raise TelephonyConfigError(
            "SIP_INBOUND_AUTH_PASSWORD must be at least "
            f"{MIN_AUTH_PASSWORD_LENGTH} characters."
        )

    room_prefix = env.get("SIP_ROOM_PREFIX", "").strip().lower() or "call-"
    if not _ROOM_PREFIX.match(room_prefix):
        raise TelephonyConfigError(
            "SIP_ROOM_PREFIX must be 1 to 24 lowercase letters, digits or '-'."
        )

    confirmed = frozenset(item.lower() for item in _csv(env, "TELEPHONY_CONFIRMED"))
    unknown = sorted(confirmed - CONFIRMATIONS.keys())
    if unknown:
        raise TelephonyConfigError(
            f"TELEPHONY_CONFIRMED has unknown entries: {', '.join(unknown)}. "
            f"Valid entries: {', '.join(sorted(CONFIRMATIONS))}."
        )

    default_region, default_auth = "global", "digest"
    if provider:
        default_region = PROVIDERS[provider].required_sip_region or "global"
        default_auth = PROVIDERS[provider].default_auth_mode

    return TelephonySettings(
        provider=provider,
        inbound_number=number,
        inbound_numbers=numbers,
        sip_subdomain=subdomain,
        sip_region=_choice(env, "LIVEKIT_SIP_REGION", default_region, SIP_REGIONS),
        transport=_choice(env, "SIP_INBOUND_TRANSPORT", "tls", TRANSPORTS),
        auth_mode=_choice(env, "SIP_INBOUND_AUTH_MODE", default_auth, AUTH_MODES),
        auth_username=username,
        auth_password=password,
        allowed_addresses=_addresses(env, "SIP_INBOUND_ALLOWED_ADDRESSES"),
        media_encryption=_choice(
            env, "SIP_MEDIA_ENCRYPTION", "require", MEDIA_ENCRYPTION
        ),
        ringing_timeout_seconds=_bounded_int(
            env, "SIP_RINGING_TIMEOUT_SECONDS", 25, 5, 60
        ),
        max_call_duration_seconds=_bounded_int(
            env, "SIP_MAX_CALL_DURATION_SECONDS", 600, 60, 3600
        ),
        hide_caller_number=_bool(env, "SIP_HIDE_CALLER_NUMBER", True),
        room_prefix=room_prefix,
        confirmed=confirmed,
    )
