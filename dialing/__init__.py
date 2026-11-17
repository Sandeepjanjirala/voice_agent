"""Outbound dialing policy, shared by Django and the voice worker.

Pure Python: no Django, no LiveKit. Both processes load it from the same
environment, so a number Django may dial is a number the worker will accept.

OUTBOUND_DIALING_MODE
    allowlist (default)  only numbers in OUTBOUND_ALLOWLIST_NUMBERS
    live                 any valid E.164 number, subject to the callers'
                         own checks (consent, calling hours, limits)
OUTBOUND_ALLOWLIST_NUMBERS
    Comma-separated E.164 numbers. When it is absent or empty, the older
    OUTBOUND_TEST_ALLOWED_NUMBER is used, so an existing installation keeps
    working after the upgrade. With neither, nothing can be dialled.

Messages never contain a phone number.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

E164 = re.compile(r"\+[1-9][0-9]{7,14}\Z")
MODES = ("allowlist", "live")


class DialingPolicyError(ValueError):
    """A refused destination or a bad setting. The message is safe to show."""


@dataclass(frozen=True)
class DialingPolicy:
    mode: str = "allowlist"
    numbers: tuple[str, ...] = field(default=(), repr=False)

    @property
    def first_number(self) -> str:
        """The first approved number, used for rehearsals; "" if none."""
        return self.numbers[0] if self.numbers else ""

    def validate(self, destination) -> str:
        """The destination if it may be dialled; otherwise DialingPolicyError."""
        if self.mode not in MODES:
            raise DialingPolicyError("OUTBOUND_DIALING_MODE must be allowlist or live.")
        if not isinstance(destination, str) or not E164.fullmatch(destination):
            raise DialingPolicyError(
                "Enter the number in international format: + then the country "
                "code and number, with no spaces."
            )
        if self.mode == "allowlist" and destination not in self.numbers:
            raise DialingPolicyError(
                "Calling blocked: this number is not in OUTBOUND_ALLOWLIST_NUMBERS. "
                "Ask an administrator to approve it."
            )
        return destination


def load_policy(env: Mapping[str, str]) -> DialingPolicy:
    mode = (env.get("OUTBOUND_DIALING_MODE") or "").strip() or "allowlist"
    if mode not in MODES:
        raise DialingPolicyError("OUTBOUND_DIALING_MODE must be allowlist or live.")
    source = "OUTBOUND_ALLOWLIST_NUMBERS"
    if not (env.get(source) or "").strip():
        source = "OUTBOUND_TEST_ALLOWED_NUMBER"
    raw = env.get(source) or ""
    numbers = tuple(dict.fromkeys(n.strip() for n in raw.split(",") if n.strip()))
    if any(not E164.fullmatch(n) for n in numbers):
        raise DialingPolicyError(
            f"{source} must hold E.164 numbers (+ country code and number), "
            "separated by commas."
        )
    return DialingPolicy(mode, numbers)
