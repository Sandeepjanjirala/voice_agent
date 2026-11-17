"""Integration credentials, encrypted with the same keys as contact numbers
(apps/crm/crypto.py), so one backup and rotation procedure covers both
(docs/CONTACT_KEYS.md). A secret is written once and never shown again."""

import json

from apps.crm import crypto


def store(integration, values: dict[str, str]) -> None:
    """Merge non-empty values into the integration's stored secrets.
    An empty value keeps what is stored, so a form never has to show it."""
    current = read(integration)
    for name, value in values.items():
        if value:
            current[name] = value
    integration.secret_encrypted = (
        crypto.encrypt(json.dumps(current)) if current else ""
    )
    integration.secret_hint = ", ".join(
        f"{name} …{value[-4:]}" for name, value in sorted(current.items()) if value
    )[:40]


def read(integration) -> dict[str, str]:
    if not integration.secret_encrypted:
        return {}
    return json.loads(crypto.decrypt(integration.secret_encrypted))
