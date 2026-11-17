"""Every dashboard query goes through a Scope.

A Scope says which organisation the request is working in and which campuses
it may see. Views never filter by organisation or campus by hand: they ask the
scope, so one place decides isolation. A record in another organisation or
campus is not "forbidden", it simply is not found.

Roles map to named permissions here, so a new permission or role is one line.
"""

from dataclasses import dataclass
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, QuerySet
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache

from .models import Campus, Membership, Organisation

Role = Membership.Role
PLATFORM = "platform"
SESSION_KEY = "organisation_id"

CAMPUS_STAFF_PERMISSIONS = frozenset(
    {
        "leads.view",
        "leads.edit",
        "contacts.view",
        "contacts.edit",
        "contacts.import",
        "optout.add",
        "calls.view",
        "reports.view",
        "employees.view",
        "campuses.view",
    }
)
ORG_ADMIN_PERMISSIONS = CAMPUS_STAFF_PERMISSIONS | frozenset(
    {
        "organisation.manage",
        "employees.manage",
        "contacts.export",
        "contacts.erase",
        "calls.export",
        "optout.remove",
        "audit.view",
        "retention.manage",
    }
)
PERMISSIONS = {
    Role.ORG_ADMIN: ORG_ADMIN_PERMISSIONS,
    Role.CAMPUS_STAFF: CAMPUS_STAFF_PERMISSIONS,
    PLATFORM: ORG_ADMIN_PERMISSIONS,
}


@dataclass(frozen=True)
class Scope:
    user: object
    organisation: Organisation
    role: str
    # None means every campus of the organisation.
    campus_ids: frozenset | None = None

    @property
    def role_label(self) -> str:
        return "Platform superuser" if self.role == PLATFORM else Role(self.role).label

    @property
    def all_campuses(self) -> bool:
        return self.campus_ids is None

    def can(self, permission: str) -> bool:
        return permission in PERMISSIONS[self.role]

    def campuses(self) -> QuerySet:
        found = Campus.objects.filter(organisation=self.organisation)
        if self.campus_ids is not None:
            found = found.filter(pk__in=self.campus_ids)
        return found

    def limit(self, queryset: QuerySet, campus_field: str = "campus") -> QuerySet:
        """Rows of this organisation that this user may see. A row with no
        campus belongs to the whole organisation: campus staff do not see it."""
        queryset = queryset.filter(organisation=self.organisation)
        if self.campus_ids is not None:
            queryset = queryset.filter(**{f"{campus_field}__in": self.campus_ids})
        return queryset

    def limit_shared(self, queryset: QuerySet, campuses_field: str = "campuses"):
        """Like limit(), for rows that list several campuses: a row with none
        is organisation-wide and visible to everyone in the organisation."""
        queryset = queryset.filter(organisation=self.organisation)
        if self.campus_ids is not None:
            queryset = queryset.filter(
                Q(**{f"{campuses_field}__in": self.campus_ids})
                | Q(**{f"{campuses_field}__isnull": True})
            ).distinct()
        return queryset

    def get(self, queryset: QuerySet, **lookup):
        """One visible row, or 404, whoever asks and whatever ID they typed."""
        try:
            found = queryset.filter(**lookup).first()
        except (ValidationError, ValueError):
            found = None
        if found is None:
            raise Http404
        return found

    def campus(self, pk):
        return self.get(self.campuses(), pk=pk)

    def members(self) -> QuerySet:
        return Membership.objects.filter(organisation=self.organisation)


def _resolve(request) -> Scope | None:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_active:
        return None
    if user.is_superuser:
        # The platform role works in one organisation at a time, chosen in the
        # sidebar; it is not a member of any.
        chosen = request.session.get(SESSION_KEY)
        organisation = None
        if chosen:
            try:
                organisation = Organisation.objects.filter(pk=chosen).first()
            except (ValidationError, ValueError):
                organisation = None
        organisation = organisation or Organisation.objects.first()
        return Scope(user, organisation, PLATFORM) if organisation else None
    membership = (
        Membership.objects.select_related("organisation").filter(user=user).first()
    )
    if membership is None:
        return None
    campus_ids = None
    if membership.role == Role.CAMPUS_STAFF:
        campus_ids = frozenset(
            membership.campuses.filter(
                organisation=membership.organisation, is_active=True
            ).values_list("pk", flat=True)
        )
    return Scope(user, membership.organisation, membership.role, campus_ids)


def scope_for(request) -> Scope | None:
    """The request's scope, worked out once per request."""
    if not hasattr(request, "_scope"):
        request._scope = _resolve(request)
    return request._scope


def organisation_of(user) -> Organisation | None:
    """The organisation a member belongs to (None for anyone else)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    membership = Membership.objects.filter(user=user).first()
    return membership.organisation if membership else None


def scoped(permission: str | None = None):
    """A dashboard view: signed in, inside an organisation, and allowed.
    The view receives the scope as `request.scope`."""

    def decorator(view):
        @never_cache
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            scope = scope_for(request)
            if scope is None:
                if request.user.is_superuser:
                    return redirect("organisation-setup")
                raise PermissionDenied
            if permission and not scope.can(permission):
                raise PermissionDenied
            request.scope = scope
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
