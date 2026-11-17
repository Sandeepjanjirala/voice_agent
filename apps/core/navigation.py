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
# "Phone calls" keeps its label: the browser tests look for it.
ANYONE, MEMBER, STAFF = "anyone", "member", "staff"
SECTIONS = (
    ("", (("home", "Home", MEMBER),)),
    (
        "Team",
        (
            ("employees", "AI employees", "employees.view"),
            ("employee-hire", "Hire an employee", "employees.manage"),
            ("campuses", "Campuses & staff", "campuses.view"),
        ),
    ),
    (
        "Outcomes",
        (
            ("leads", "Leads & contacts", "leads.view"),
            ("conversations", "Conversations", "calls.view"),
            ("performance", "Performance", "reports.view"),
        ),
    ),
    (
        "Calling",
        (
            ("campaigns", "Campaigns", "campaigns.view"),
            ("instant", "Instant leads", "sources.view"),
            ("inbound", "Incoming calls", "calls.view"),
            ("calls", "Phone calls", STAFF),
        ),
    ),
    (
        "Setup",
        (
            ("numbers", "Numbers & channels", "numbers.view"),
            ("knowledge", "Knowledge", "knowledge.view"),
            ("settings", "Privacy & settings", MEMBER),
        ),
    ),
    ("Tools", (("voice", "Voice studio", ANYONE), ("agent", "Agent profile", STAFF))),
)
# Pages that belong to a sidebar entry without being one.
PARENTS = {
    "employee-detail": "employees",
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
    "campaign-new": "campaigns",
    "campaign-detail": "campaigns",
    "source-new": "instant",
    "source-detail": "instant",
    "number-edit": "numbers",
    "knowledge-edit": "knowledge",
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
    sections = []
    for title, entries in SECTIONS:
        items = [
            {
                "label": label,
                "url": reverse(name),
                "current": name == current,
                "soon": name in MODULES,
            }
            for name, label, requirement in entries
            if _visible(requirement, user, scope)
        ]
        if items:
            sections.append({"title": title, "items": items})
    organisations = ()
    if user.is_superuser and scope is not None:
        organisations = Organisation.objects.all()
    return {
        "nav_sections": sections,
        "scope": scope,
        "switch_organisations": organisations,
    }
