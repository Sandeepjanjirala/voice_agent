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
    # Everything else, folded away until it is needed.
    (
        "More",
        (
            ("leads", "Leads & contacts", "leads.view"),
            ("campuses", "Campuses & staff", "campuses.view"),
            ("performance", "Performance", "reports.view"),
            ("inbound", "Incoming calls", "calls.view"),
            ("numbers", "Numbers & channels", "numbers.view"),
            ("knowledge", "Knowledge", "knowledge.view"),
            ("usage", "Usage & costs", "usage.view"),
            ("operations", "Operations", "operations.view"),
        ),
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
    # "More" stays folded unless one of its pages is open, or it is all there is.
    for section in sections:
        section["folded"] = section["title"] == "More"
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
    current_label = next(
        (
            item["label"]
            for section in sections
            for item in section["items"]
            if item["current"]
        ),
        "",
    )
    return {
        "nav_sections": sections,
        "nav_current_label": current_label,
        "scope": scope,
        "switch_organisations": organisations,
        "unread_notifications": unread,
    }
