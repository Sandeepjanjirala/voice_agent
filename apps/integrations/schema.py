"""A small, strict subset of JSON Schema for action inputs.

Supported: an object with "properties" and "required"; each property has a
"type" of string, integer, number or boolean, and optionally "maxLength",
"minimum", "maximum", "enum", "pattern" or "format" (date, time, date-time,
phone). Unknown input names are refused, not ignored, and strings are capped
at 500 characters even without a maxLength.
"""

import re
from datetime import date, datetime, time

from apps.crm.phones import InvalidPhoneError, normalize

TYPES = ("string", "integer", "number", "boolean")
FORMATS = ("date", "time", "date-time", "phone")
STRING_CAP = 500


class SchemaError(ValueError):
    """The schema or the inputs are not valid. The message is safe to show."""


def check_schema(schema) -> dict:
    """A schema an admin may save."""
    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        raise SchemaError('The schema must be an object with "properties".')
    properties = schema.get("properties", {})
    if not isinstance(properties, dict) or len(properties) > 20:
        raise SchemaError("Give up to 20 properties.")
    for name, rule in properties.items():
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,40}", name):
            raise SchemaError(f"{name!r}: use lowercase letters, digits and _.")
        if not isinstance(rule, dict) or rule.get("type") not in TYPES:
            raise SchemaError(f"{name}: type must be one of {', '.join(TYPES)}.")
        if rule.get("format") not in (None, *FORMATS):
            raise SchemaError(f"{name}: format must be one of {', '.join(FORMATS)}.")
        if "pattern" in rule:
            try:
                re.compile(rule["pattern"])
            except (re.error, TypeError):
                raise SchemaError(f"{name}: the pattern is not valid.") from None
    required = schema.get("required", [])
    if not isinstance(required, list) or any(r not in properties for r in required):
        raise SchemaError('"required" must list property names.')
    return {"type": "object", "properties": properties, "required": required}


def _value(name: str, rule: dict, value):
    kind = rule["type"]
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if value in ("true", "on", "1"):
            return True
        if value in ("false", "", "0", None):
            return False
        raise SchemaError(f"{name} must be true or false.")
    if kind in ("integer", "number"):
        try:
            number = int(value) if kind == "integer" else float(value)
        except (TypeError, ValueError):
            raise SchemaError(f"{name} must be a number.") from None
        if isinstance(value, bool):
            raise SchemaError(f"{name} must be a number.")
        if "minimum" in rule and number < rule["minimum"]:
            raise SchemaError(f"{name} must be at least {rule['minimum']}.")
        if "maximum" in rule and number > rule["maximum"]:
            raise SchemaError(f"{name} must be at most {rule['maximum']}.")
        return number
    if not isinstance(value, str | int | float) or isinstance(value, bool):
        raise SchemaError(f"{name} must be text.")
    text = " ".join(str(value).split())
    limit = min(int(rule.get("maxLength", STRING_CAP)), STRING_CAP)
    if len(text) > limit:
        raise SchemaError(f"{name} is longer than {limit} characters.")
    if "enum" in rule and text not in rule["enum"]:
        raise SchemaError(f"{name} must be one of {', '.join(map(str, rule['enum']))}.")
    if "pattern" in rule and not re.fullmatch(rule["pattern"], text):
        raise SchemaError(f"{name} is not in the expected form.")
    fmt = rule.get("format")
    try:
        if fmt == "date":
            date.fromisoformat(text)
        elif fmt == "time":
            time.fromisoformat(text)
        elif fmt == "date-time":
            datetime.fromisoformat(text)
        elif fmt == "phone":
            text = normalize(text)
    except (ValueError, InvalidPhoneError):
        raise SchemaError(f"{name} is not a valid {fmt}.") from None
    return text


def validate(schema: dict, inputs) -> dict:
    """Clean inputs, or SchemaError. Missing optional inputs are left out."""
    if not isinstance(inputs, dict):
        raise SchemaError("Inputs must be an object.")
    properties = schema.get("properties", {})
    unknown = sorted(set(inputs) - set(properties))
    if unknown:
        raise SchemaError(f"Unknown input: {', '.join(unknown)}.")
    clean = {}
    for name, rule in properties.items():
        value = inputs.get(name)
        if value in (None, "") and rule["type"] != "boolean":
            if name in schema.get("required", []):
                raise SchemaError(f"{name} is required.")
            continue
        clean[name] = _value(name, rule, value)
    return clean
