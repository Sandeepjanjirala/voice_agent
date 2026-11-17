"""From an employee's configuration to what the voice worker is told.

The worker (voice_agent/profile.py, which Django never imports) accepts one
small profile per call and clamps every field. Publishing checks the same
limits first, so nothing is cut silently and a version that is saved is exactly
what a call will use.
"""

from typing import Any

from django.db import transaction

from apps.telephony import profiles
from apps.telephony.models import AgentProfile

from .config import normalize
from .models import Employee, EmployeeVersion

# Kept in step with voice_agent/profile.py; a test compares the two.
PROFILE_KEY = profiles.PROFILE_KEY
WORKER_LIMITS = {
    "business_name": 80,
    "agent_name": 40,
    "purpose": 300,
    "greeting": 250,
    "objectives": 400,
    "limitations": 300,
    "ending": 200,
    "business_info": 4000,
}
# Bytes shared by the prompt fields; the worker's section budget is 1024.
PROMPT_BYTES = AgentProfile.PROMPT_BYTES
PROMPT_FIELDS = ("agent_name", "purpose", "objectives", "limitations", "ending")
FIELD_LABELS = {
    "objectives": "The steps and details to collect",
    "limitations": "What it must not do, with the strictness setting",
    "purpose": "Why it is talking to the person",
    "ending": "When and how to end the call",
    "greeting": "The opening line",
    "agent_name": "The name",
}
_STRICTNESS = {
    1: "Treat the goals as a loose guide and follow the caller's lead.",
    2: "Keep to the goals, but let the conversation flow naturally.",
    3: "",
    4: "Follow the goals in the order given.",
    5: "Follow the goals in order and keep close to their wording; do not improvise.",
}


def _business() -> tuple[str, str]:
    """Business name and facts. They still live on the Agent profile page."""
    profile = profiles.active_profile()
    return (profile.business_name, profile.business_info) if profile else ("", "")


def worker_payload(config: dict) -> dict[str, str]:
    """The profile the voice worker receives for this configuration."""
    config = normalize(config)
    goals = " ".join(
        f"{number}. {step['guidance'] or step['name']}"
        for number, step in enumerate(config["steps"], start=1)
    )
    if config["questions"]:
        labels = ", ".join(question["label"] for question in config["questions"])
        goals = f"{goals} Find out: {labels}."
    business_name, business_info = _business()
    return {
        "business_name": business_name,
        "agent_name": config["agent_name"],
        "purpose": config["purpose"],
        "greeting": config["greeting"],
        "objectives": goals.strip(),
        "limitations": " ".join(
            part
            for part in (config["limits"], _STRICTNESS[config["strictness"]])
            if part
        ),
        "ending": config["ending"],
        "business_info": business_info,
    }


def prompt_size(config: dict) -> int:
    payload = worker_payload(config)
    return sum(len(payload[field].encode("utf-8")) for field in PROMPT_FIELDS)


def problems(config: dict) -> list[str]:
    """Why this configuration cannot be used on a call. Empty means it can."""
    config = normalize(config)
    found = []
    if not config["agent_name"]:
        found.append("Give the employee a name.")
    if not config["greeting"]:
        found.append("Write the opening line.")
    if not config["purpose"]:
        found.append("Say why it is talking to the person.")
    if not config["steps"]:
        found.append("Add at least one conversation step.")
    payload = worker_payload(config)
    for field, label in FIELD_LABELS.items():
        over = len(payload[field]) - WORKER_LIMITS[field]
        if over > 0:
            found.append(f"{label} is {over} characters too long. Shorten it.")
    size = prompt_size(config)
    if size > PROMPT_BYTES:
        found.append(
            f"The script is {size} bytes; the voice agent takes {PROMPT_BYTES} "
            "(Telugu letters count as three). Shorten the steps, limits or ending."
        )
    return found


class PublishError(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__(" ".join(reasons))
        self.reasons = reasons


def has_changes(employee: Employee) -> bool:
    """True when the draft differs from what calls use."""
    live = employee.live_version
    return live is None or normalize(employee.draft) != live.config


def publish(employee: Employee, user=None) -> EmployeeVersion:
    """Save the draft as a new version and make it the one calls use."""
    config = normalize(employee.draft)
    reasons = problems(config)
    if reasons:
        raise PublishError(reasons)
    with transaction.atomic():
        locked = Employee.objects.select_for_update().get(pk=employee.pk)
        latest = locked.versions.first()
        version = EmployeeVersion.objects.create(
            employee=locked,
            number=latest.number + 1 if latest else 1,
            config=config,
            payload=worker_payload(config),
            published_by=user if getattr(user, "is_authenticated", False) else None,
        )
        first = locked.live_version_id is None
        locked.live_version = version
        if first:
            locked.taking_calls = True
        locked.save(update_fields=["live_version", "taking_calls", "updated_at"])
    employee.refresh_from_db()
    return version


def discard_changes(employee: Employee) -> None:
    """Put the draft back to what calls use."""
    if employee.live_version is None:
        raise PublishError(
            ["Nothing is published yet, so there is nothing to go back to."]
        )
    copy_into_draft(employee, employee.live_version)


def copy_into_draft(employee: Employee, version: EmployeeVersion) -> None:
    employee.draft = version.config
    employee.name = version.config["agent_name"] or employee.name
    employee.role = version.config["role"] or employee.role
    employee.save(update_fields=["draft", "name", "role", "updated_at"])


def call_profile(employee: Employee) -> tuple[EmployeeVersion, dict[str, Any]]:
    """The version a phone call must use, and its job metadata.

    Refuses unless the employee is published and switched on: a draft is never
    used on a call, and a paused employee takes none.
    """
    version = employee.live_version
    if version is None:
        raise PublishError([f"{employee.name} has not been published yet."])
    if not employee.taking_calls:
        raise PublishError([f"{employee.name} is paused. Switch it on first."])
    return version, trial_metadata(employee, "published")


def trial_metadata(employee: Employee, source: str) -> dict[str, Any]:
    """Job metadata for trying an employee in the voice studio.

    "draft" uses the working copy, checked the same way publishing is;
    "published" uses the stored payload of the version calls use.
    """
    if source == "published":
        if employee.live_version is None:
            raise PublishError([f"{employee.name} has not been published yet."])
        payload = dict(employee.live_version.payload)
        # Business facts may have changed since publishing; use today's.
        payload["business_name"], payload["business_info"] = _business()
        return {PROFILE_KEY: payload}
    reasons = problems(employee.draft)
    if reasons:
        raise PublishError(reasons)
    return {PROFILE_KEY: worker_payload(employee.draft)}
