"""Phone numbers as typed by people, turned into E.164 or refused."""

import re

DEFAULT_COUNTRY = "91"
_INDIAN_MOBILE = re.compile(r"[6-9]\d{9}")


class InvalidPhoneError(ValueError):
    pass


def normalize(raw) -> str:
    """E.164 for a number written in any common way, e.g. +919876543210.

    Without a country code the number is taken to be Indian. Indian numbers
    must be ten digits starting 6 to 9; other countries need a leading "+".
    """
    text = str(raw or "").strip()
    if not text:
        raise InvalidPhoneError("The phone number is empty.")
    if re.search(r"[^\d\s+().\-]", text):
        raise InvalidPhoneError("A phone number has only digits, spaces and +.")
    international = text.startswith("+") or text.startswith("00")
    digits = re.sub(r"\D", "", text)
    if text.startswith("00"):
        digits = digits[2:]
    if not international:
        if len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) == 12 and digits.startswith(DEFAULT_COUNTRY):
            digits = digits[2:]
        if not _INDIAN_MOBILE.fullmatch(digits):
            raise InvalidPhoneError(
                "Enter a ten-digit mobile number, or a number starting with +."
            )
        return f"+{DEFAULT_COUNTRY}{digits}"
    if digits.startswith(DEFAULT_COUNTRY):
        if not _INDIAN_MOBILE.fullmatch(digits[2:]):
            raise InvalidPhoneError("That is not a valid Indian mobile number.")
    elif not 8 <= len(digits) <= 15 or digits.startswith("0"):
        raise InvalidPhoneError("That is not a valid international number.")
    return f"+{digits}"
