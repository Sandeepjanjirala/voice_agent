"""Phone numbers are personal data: never write one to a log or console in full."""

import re

_PHONE_RUN = re.compile(r"\+?\d[\d\s\-]{6,}\d")


def mask_phone(number: str | None) -> str:
    """Keep the country prefix and the last two digits: +91********10."""
    if not number:
        return "(none)"
    digits = re.sub(r"\D", "", number)
    if len(digits) < 7:
        return "*" * len(digits)
    plus = "+" if number.strip().startswith("+") else ""
    return f"{plus}{digits[:2]}{'*' * (len(digits) - 4)}{digits[-2:]}"


def mask_phones_in(text: str) -> str:
    """Mask every phone-number-like run inside free text, such as an error message."""
    return _PHONE_RUN.sub(lambda match: mask_phone(match.group()), text)
