"""Facts and rules an AI employee is given on a call, by campus.

Compiled into the worker's business-information lookup (voice_agent/profile.py),
which the employee calls before stating any fact; the text is never put in the
per-turn prompt, so it can be long without slowing every reply.
"""

from django.db.models import Q

from .models import KnowledgeItem

# The worker accepts this much business information per call.
LIMIT = 4000


def items_for(organisation, campus=None):
    """Active items for a call at `campus`: its own, and the organisation's.
    Without a campus, only the organisation-wide items."""
    found = KnowledgeItem.objects.filter(organisation=organisation, is_active=True)
    if campus is None:
        return found.filter(campus__isnull=True)
    return found.filter(Q(campus=campus) | Q(campus__isnull=True))


def compile_for(organisation, campus=None) -> str:
    """The text the employee looks up: facts, then rules. Cut, never mid-item,
    to fit the worker's limit."""
    sections = []
    for kind, heading in (
        (KnowledgeItem.Kind.FACT, "Facts you may state:"),
        (KnowledgeItem.Kind.RULE, "Rules you must follow:"),
    ):
        lines = [
            f"- {item.title}: {item.text}"
            for item in items_for(organisation, campus).filter(kind=kind)
        ]
        if lines:
            sections.append("\n".join([heading, *lines]))
    text = ""
    for section in sections:
        for line in section.split("\n"):
            candidate = f"{text}\n{line}" if text else line
            if len(candidate) > LIMIT:
                return text
            text = candidate
        text += "\n"
    return text.strip()
