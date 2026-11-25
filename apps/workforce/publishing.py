"""From an employee's configuration to what the voice worker is told.

The worker (voice_agent/profile.py, which Django never imports) accepts one
small profile per call and clamps every field. Publishing checks the same
limits first, so nothing is cut silently and a version that is saved is exactly
what a call will use.
"""

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.telephony import profiles

from . import knowledge
from .config import normalize
from .models import Employee, EmployeeVersion
from .voices import SPEECH_LANGUAGE

# Kept in step with voice_agent/profile.py; a test compares the two.
PROFILE_KEY = profiles.PROFILE_KEY
WORKER_LIMITS = {
    "business_name": 160,
    "agent_name": 60,
    "purpose": 1200,
    "greeting": 400,
    "objectives": 9000,
    "limitations": 1800,
    "ending": 600,
    "business_info": 8000,
    "success": 600,
    "situations": 4000,
    "escalation": 600,
    "rules": 1200,
}


def instruction_budget() -> int:
    """Bytes the instruction fields may take together (EMPLOYEE_INSTRUCTION_BYTES)."""
    return settings.EMPLOYEE_INSTRUCTION_BYTES


# Kept for existing imports; the live value is instruction_budget().
PROMPT_BYTES = 6144
PROMPT_FIELDS = (
    "agent_name",
    "purpose",
    "objectives",
    "limitations",
    "ending",
    "success",
    "situations",
    "escalation",
)
FIELD_LABELS = {
    "objectives": "The steps and details to collect",
    "success": "What a successful call achieves",
    "situations": "The situations and what to do",
    "escalation": "When to hand over to a person",
    "business_info": "The information it may give, with the organisation's facts",
    "limitations": "What it must not do, with the strictness setting",
    "purpose": "Why it is talking to the person",
    "ending": "When and how to end the call",
    "greeting": "The opening line",
    "agent_name": "The name",
}
STRICTNESS_SENTENCES = {
    1: "Treat the goals as a loose guide and follow the caller's lead.",
    2: "Keep to the goals, but let the conversation flow naturally.",
    3: "",
    4: "Follow the goals in the order given.",
    5: "Follow the goals in order and keep close to their wording; do not improvise.",
}


def worker_payload(config: dict, language: str = "") -> dict[str, Any]:
    """The profile the voice worker receives for this configuration.
    `language` is Employee.language; it sets the voice's language."""
    config = normalize(config)
    goals = " ".join(
        f"{number}. {step['guidance'] or step['name']}"
        for number, step in enumerate(config["steps"], start=1)
    )
    if config["questions"]:
        labels = ", ".join(question["label"] for question in config["questions"])
        goals = f"{goals} Find out: {labels}."
    return {
        "business_name": "",
        "agent_name": config["agent_name"],
        "purpose": config["purpose"],
        "greeting": config["greeting"],
        "objectives": goals.strip(),
        "limitations": " ".join(
            part
            for part in (config["limits"], STRICTNESS_SENTENCES[config["strictness"]])
            if part
        ),
        "ending": config["ending"],
        "success": config["success"],
        "situations": situations_text(config["situations"]),
        "escalation": config["escalation"],
        # Looked up by the employee when asked (get_business_info), not sent
        # on every turn. Kept apart so today's Knowledge can be added per call.
        "information": config["information"],
        "business_info": config["information"],
        "voice": {
            "speaker": config["voice"]["speaker"],
            "pace": config["voice"]["pace"],
            "language": SPEECH_LANGUAGE.get(language, ""),
        },
    }


def situations_text(situations: list[dict]) -> str:
    """ "When they ask about fees: …; When …" as one line for the prompt.
    Kept in step with apps/core/static/dashboard/budget.js, which mirrors it."""
    parts = []
    for item in situations:
        when = item["when"]
        if not when.lower().startswith(("when ", "if ")):
            when = f"When {when}"
        parts.append(f"{when}: {item['then']}")
    return "; ".join(parts)


def _joined(*texts: str) -> str:
    return "\n".join(text for text in texts if text)


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
    size, budget = prompt_size(config), instruction_budget()
    if size > budget:
        found.append(
            f"The instructions take {size:,} of {budget:,} bytes (a Telugu letter "
            f"takes three, so about {(size - budget) // 3 + 1:,} Telugu letters too "
            "many). Shorten the longest step, or move facts into Knowledge, which the "
            "employee looks up only when asked."
        )
    return found


class PublishError(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__(" ".join(reasons))
        self.reasons = reasons


def has_changes(employee: Employee) -> bool:
    """True when the draft differs from what calls use."""
    live = employee.live_version
    return live is None or normalize(employee.draft) != normalize(live.config)


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
            payload=worker_payload(config, locked.language),
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


def _business_name(employee: Employee, campus) -> str:
    organisation = employee.organisation
    if organisation is None:
        return ""
    name = f"{organisation.name}, {campus.name}" if campus else organisation.name
    return name[: WORKER_LIMITS["business_name"]]


def _with_knowledge(payload: dict, employee: Employee, campus) -> dict:
    """The payload with today's business name and knowledge for this campus.
    Knowledge applies from the next call, without publishing again."""
    payload = dict(payload)
    # Already part of business_info; kept in the version only so today's
    # Knowledge can be added to it here. The worker never needs it.
    information = payload.pop("information", "")
    payload["business_name"] = _business_name(employee, campus)
    if employee.organisation is not None:
        payload["observability"] = employee.organisation.cloud_observability
    if employee.organisation is not None:
        # Only this organisation's own facts: its Knowledge and what the
        # employee was given. The Agent profile page is one setting for the
        # whole installation, so its facts would reach other organisations.
        facts = knowledge.compile_for(employee.organisation, campus)
        payload["business_info"] = _joined(information, facts)[
            : WORKER_LIMITS["business_info"]
        ]
        payload["rules"] = knowledge.rules_for(employee.organisation, campus)
    return payload


@dataclass(frozen=True)
class CallPlan:
    """Everything a call needs, built once when it starts."""

    version: EmployeeVersion
    metadata: dict[str, Any]
    snapshot: dict[str, Any]


def plan_call(
    employee: Employee,
    *,
    campus=None,
    lead=None,
    rehearsal: bool = False,
    caller_name: str = "",
    about: str = "",
) -> CallPlan:
    """The published version a phone call uses, its job metadata, and the
    snapshot stored with the call record.

    Refuses unless the employee is published and switched on: a draft is never
    used on a call, and a paused employee takes none. Later edits to the draft
    cannot reach a call that has started, because the call is given this copy.
    """
    version = employee.live_version
    if version is None:
        raise PublishError([f"{employee.name} has not been published yet."])
    if not employee.taking_calls:
        raise PublishError([f"{employee.name} is paused. Switch it on first."])
    config = normalize(version.config)
    payload = _with_knowledge(version.payload, employee, campus)
    payload.setdefault("voice", worker_payload(config, employee.language)["voice"])
    context = {}
    contact = getattr(lead, "contact", None)
    name = caller_name or (contact.name if contact and not contact.is_erased else "")
    if name:
        context["name"] = name
    if lead is not None:
        context["about"] = lead.subject
    elif about:
        context["about"] = about[:160]
    if context:
        payload["context"] = context
    organisation = employee.organisation
    if organisation is not None and organisation.transcripts != "off":
        payload["transcript"] = True
    if organisation is not None:
        payload["observability"] = organisation.cloud_observability
    snapshot = {
        "built_at": timezone.now().isoformat(),
        "employee": {
            "id": str(employee.pk),
            "name": employee.name,
            "role": employee.role,
            "version": version.number,
        },
        "campus": campus.name if campus else "",
        "lead": str(lead.pk) if lead is not None else "",
        "rehearsal": rehearsal,
        "questions": config["questions"],
        # The results the call report chooses from (analysis.py).
        "outcomes": config["outcomes"],
        "actions": config["actions"],
        "profile": payload,
    }
    return CallPlan(version, {PROFILE_KEY: payload}, snapshot)


def call_profile(employee: Employee) -> tuple[EmployeeVersion, dict[str, Any]]:
    """The version a phone call must use, and its job metadata."""
    plan = plan_call(employee)
    return plan.version, plan.metadata
