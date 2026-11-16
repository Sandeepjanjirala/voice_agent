"""What this agent is for: purpose, goals, limits and business facts.

The profile is edited in the Django application and reaches the worker as job
metadata. The worker never trusts its size: every field is clamped here, the
prompt section has its own byte budget, and business facts stay out of the
prompt behind a tool, so the per-turn prompt stays small.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from livekit.agents import function_tool

PROFILE_KEY = "agent_profile"
# Budget for the per-call section added to the system prompt.
PROFILE_BUDGET_BYTES = 1024

_LIMITS = {
    "business_name": 80,
    "agent_name": 40,
    "purpose": 300,
    "greeting": 250,
    "objectives": 400,
    "limitations": 300,
    "ending": 200,
    "business_info": 4000,
}

NO_INFO_REPLY = (
    "No business information is available. Say our office team will call back "
    "with the details. Do not guess."
)


@dataclass(frozen=True)
class AgentProfile:
    business_name: str = ""
    agent_name: str = ""
    purpose: str = ""
    greeting: str = ""
    objectives: str = ""
    limitations: str = ""
    ending: str = ""
    business_info: str = ""

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.purpose,
                self.objectives,
                self.limitations,
                self.ending,
                self.greeting,
            )
        ) and not (self.business_name or self.business_info)


def parse_profile(metadata: str | None) -> AgentProfile | None:
    """The agent profile in job metadata, or None when there is none."""
    try:
        data = json.loads(metadata) if metadata else None
    except ValueError:
        return None
    raw = data.get(PROFILE_KEY) if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return None
    fields = {
        name: " ".join(str(raw.get(name) or "").split())[:limit]
        if name != "business_info"
        else str(raw.get(name) or "").strip()[:limit]
        for name, limit in _LIMITS.items()
    }
    profile = AgentProfile(**fields)
    return None if profile.is_empty else profile


def profile_instructions(profile: AgentProfile | None) -> str:
    """The "This call" prompt section, never larger than PROFILE_BUDGET_BYTES."""
    if profile is None:
        return ""
    lines = ["# This call"]
    if profile.agent_name:
        lines.append(f"- Your name is {profile.agent_name}.")
    for label, value in (
        ("Purpose", profile.purpose),
        ("Goals", profile.objectives),
        ("Limits", profile.limitations),
        ("Ending", profile.ending),
    ):
        if value:
            lines.append(f"- {label}: {value}")
    lines.append(
        "- Work towards the purpose; never ask what help the caller needs if you "
        "already know why you are talking."
    )
    if profile.business_info:
        lines.append(
            "- For any fact about the business (timings, address, services, fees), "
            "call get_business_info and use only what it returns."
        )
    # Drop whole lines from the end rather than cut a sentence in half.
    while (
        len("\n".join(lines).encode("utf-8")) > PROFILE_BUDGET_BYTES and len(lines) > 2
    ):
        lines.pop(-2 if profile.business_info and len(lines) > 3 else -1)
    return "\n".join(lines)


def business_info_tools(profile: AgentProfile | None) -> list[Any]:
    """A lookup tool for business facts, so they are not re-sent on every turn."""
    if profile is None or not profile.business_info:
        return []
    info = profile.business_info

    @function_tool
    async def get_business_info() -> str:
        """Returns the facts the business has provided: timings, address,
        services, fees and similar. Call this before stating any such fact.
        If the answer is not in what it returns, say our office team will call
        back; never guess."""
        return info or NO_INFO_REPLY

    return [get_business_info]
