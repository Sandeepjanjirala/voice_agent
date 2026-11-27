"""The dashboard's sidebar, and the modules that are planned but not built yet.

A module listed in MODULES has a real page that says plainly what works today
and what is still to come, so the sidebar never leads to a fake screen. When a
module is built, remove it from MODULES and point its URL at the real view.
"""

from django.urls import reverse

from apps.tenancy.access import scope_for
from apps.tenancy.models import Organisation

# (section title, ((url name, label, who sees it), ...)). The last value is a
# permission from apps/tenancy/access.py, or ANYONE (signed in), MEMBER (works
# in an organisation) or STAFF (Django staff: the platform's own tools).
ANYONE, MEMBER, STAFF = "anyone", "member", "staff"
SECTIONS = (
    # The everyday steps, in order: who speaks, who to call, what happened.
    (
        "",
        (
            ("home", "Home", MEMBER),
            ("employees", "AI employees", "employees.view"),
            ("make-calls", "Make calls", "calls.place"),
            ("conversations", "Call results", "calls.view"),
            ("settings", "Settings", MEMBER),
        ),
    ),
    # The three supporting tools used while preparing or following up calls.
    (
        "Workspace tools",
        (
            ("leads", "Leads & contacts", "leads.view"),
            ("knowledge", "Knowledge", "knowledge.view"),
            ("inbound", "Incoming calls", "calls.view"),
        ),
    ),
)
# Setup and reporting are reached through Settings, with their purpose beside
# each link. Keeping permissions here prevents the hub exposing admin tools.
SETTINGS_TOOLS = (
    (
        "campuses",
        "Locations & team",
        "campuses.view",
        "Manage branches, opening hours and the people who work there.",
    ),
    (
        "numbers",
        "Numbers & channels",
        "numbers.view",
        "Connect phone numbers and choose which AI employee answers each one.",
    ),
    (
        "performance",
        "Performance",
        "reports.view",
        "See call outcomes and follow-up progress across your locations.",
    ),
    (
        "usage",
        "Usage & costs",
        "usage.view",
        "Review talk time, estimated costs and your monthly budget.",
    ),
    (
        "operations",
        "Operations",
        "operations.view",
        "Check service health and investigate calls or background jobs that failed.",
    ),
)
# Pages that belong to a sidebar entry without being one.
PARENTS = {
    "employee-detail": "employees",
    "employee-hire": "employees",
    "call-list": "make-calls",
    "lead-new": "leads",
    "lead-detail": "leads",
    "contacts": "leads",
    "contact-detail": "leads",
    "contact-import": "leads",
    "followups": "leads",
    "conversation-detail": "conversations",
    "campus-edit": "campuses",
    "member-add": "campuses",
    "member-edit": "campuses",
    "audit-log": "settings",
    "number-edit": "numbers",
    "knowledge-edit": "knowledge",
    "notifications": "home",
}

MODULES: dict = {}


def _visible(requirement: str, user, scope) -> bool:
    if requirement == ANYONE:
        return True
    if requirement == STAFF:
        return user.is_staff
    if scope is None:
        return False
    return requirement == MEMBER or scope.can(requirement)


def navigation(request) -> dict:
    """Sidebar sections for the signed-in user, with the current page marked."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav_sections": ()}
    scope = scope_for(request)
    match = getattr(request, "resolver_match", None)
    current = match.url_name if match else ""
    current = PARENTS.get(current, current)
    settings_names = {name for name, *_ in SETTINGS_TOOLS}
    current_section = "settings" if current in settings_names else current
    sections = []
    for title, entries in SECTIONS:
        items = [
            {
                "label": label,
                "url": reverse(name),
                "current": name == current_section,
                "soon": name in MODULES,
            }
            for name, label, requirement in entries
            if _visible(requirement, user, scope)
        ]
        if items:
            sections.append({"title": title, "items": items})
    # Supporting tools stay folded unless one is open, or they are all there is.
    for section in sections:
        section["folded"] = section["title"] == "Workspace tools"
        section["open"] = len(sections) == 1 or any(
            item["current"] for item in section["items"]
        )
    organisations = ()
    if user.is_superuser and scope is not None:
        organisations = Organisation.objects.all()
    unread = 0
    if scope is not None:
        from apps.operations.views import visible_notifications

        unread = visible_notifications(scope).filter(read_at__isnull=True).count()
    settings_tools = [
        {
            "label": label,
            "url": reverse(name),
            "description": description,
            "current": name == current,
        }
        for name, label, requirement, description in SETTINGS_TOOLS
        if _visible(requirement, user, scope)
    ]
    current_label = next(
        (
            item["label"]
            for item in settings_tools
            + [item for section in sections for item in section["items"]]
            if item["current"]
        ),
        "",
    )
    return {
        "nav_sections": sections,
        "nav_current_label": current_label,
        "settings_tools": settings_tools,
        "scope": scope,
        "switch_organisations": organisations,
        "unread_notifications": unread,
    }
