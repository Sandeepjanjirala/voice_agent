"""Organisation setup, campuses, staff and the access log."""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import audit
from .access import SESSION_KEY, scoped
from .forms import CampusForm, MemberAddForm, MemberEditForm, OrganisationSetupForm
from .models import AuditEvent, Campus, Membership, Organisation, RetentionPolicy

User = get_user_model()
AUDIT_PAGE = 50


def _adopt_unowned(organisation) -> None:
    """Records made before organisations existed join the first one created."""
    from apps.telephony.models import CallRecord
    from apps.workforce.models import Employee

    Employee.objects.filter(organisation__isnull=True).update(organisation=organisation)
    CallRecord.objects.filter(organisation__isnull=True).update(
        organisation=organisation
    )


@never_cache
@login_required
def organisation_setup(request):
    """Create an organisation. Only the platform superuser may: an
    organisation's own staff are added from Campuses & staff."""
    if not request.user.is_superuser:
        raise PermissionDenied
    form = OrganisationSetupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            first = not Organisation.objects.exists()
            organisation = Organisation.objects.create(name=form.cleaned_data["name"])
            Campus.objects.create(
                organisation=organisation, name=form.cleaned_data["campus"]
            )
            RetentionPolicy.objects.create(organisation=organisation)
            if first:
                _adopt_unowned(organisation)
        request.session[SESSION_KEY] = str(organisation.pk)
        messages.success(request, f"{organisation.name} is set up.")
        return redirect("campuses")
    return render(
        request,
        "tenancy/setup.html",
        {"form": form, "first": not Organisation.objects.exists()},
    )


@never_cache
@login_required
@require_POST
def organisation_switch(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    try:
        organisation = Organisation.objects.filter(
            pk=request.POST.get("organisation")
        ).first()
    except (ValidationError, ValueError):
        organisation = None
    if organisation:
        request.session[SESSION_KEY] = str(organisation.pk)
    return redirect("home")


@scoped("campuses.view")
def campuses(request):
    scope = request.scope
    manage = scope.can("organisation.manage")
    form = CampusForm(
        request.POST or None, organisation=scope.organisation, prefix="campus"
    )
    if request.method == "POST":
        if not manage:
            raise PermissionDenied
        if form.is_valid():
            campus = form.save()
            audit.record(scope, "campus.created", campus, name=campus.name)
            messages.success(request, f"{campus.name} is added.")
            return redirect("campuses")
    return render(
        request,
        "tenancy/campuses.html",
        {
            "campuses": scope.campuses().annotate(
                contact_count=Count("contacts", distinct=True),
                lead_count=Count("leads", distinct=True),
                staff_count=Count("memberships", distinct=True),
            ),
            "members": scope.members()
            .select_related("user")
            .prefetch_related("campuses")
            if manage
            else (),
            "form": form,
            "manage": manage,
        },
    )


@scoped("organisation.manage")
def campus_edit(request, pk):
    scope = request.scope
    campus = scope.campus(pk)
    form = CampusForm(
        request.POST or None, instance=campus, organisation=scope.organisation
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        audit.record(scope, "campus.changed", campus, in_use=campus.is_active)
        messages.success(request, f"{campus.name} is saved.")
        return redirect("campuses")
    return render(request, "tenancy/campus_edit.html", {"form": form, "campus": campus})


@scoped("organisation.manage")
def member_add(request):
    scope = request.scope
    form = MemberAddForm(request.POST or None, organisation=scope.organisation)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(
                username=data["username"],
                password=data["password"],
                first_name=data["first_name"],
            )
            membership = Membership.objects.create(
                user=user, organisation=scope.organisation, role=data["role"]
            )
            membership.campuses.set(data["campuses"])
        audit.record(
            scope, "member.added", membership, username=user.username, role=data["role"]
        )
        messages.success(request, f"{user.username} can now sign in.")
        return redirect("campuses")
    return render(request, "tenancy/member.html", {"form": form, "member": None})


@scoped("organisation.manage")
def member_edit(request, pk):
    scope = request.scope
    membership = scope.get(scope.members().select_related("user"), pk=pk)
    own = membership.user_id == scope.user.pk
    form = MemberEditForm(
        request.POST or None,
        organisation=scope.organisation,
        initial={
            "role": membership.role,
            "campuses": membership.campuses.all(),
            "is_active": membership.user.is_active,
        },
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        if own and (data["role"] != Membership.Role.ORG_ADMIN or not data["is_active"]):
            form.add_error(
                None,
                "You cannot remove your own admin role or sign-in. Ask another admin.",
            )
        else:
            membership.role = data["role"]
            membership.save(update_fields=["role"])
            membership.campuses.set(data["campuses"])
            membership.user.is_active = data["is_active"]
            membership.user.save(update_fields=["is_active"])
            audit.record(
                scope,
                "member.changed",
                membership,
                username=membership.user.username,
                role=data["role"],
                can_sign_in=data["is_active"],
            )
            messages.success(request, f"{membership.user.username} is saved.")
            return redirect("campuses")
    return render(request, "tenancy/member.html", {"form": form, "member": membership})


@scoped("audit.view")
def audit_log(request):
    scope = request.scope
    events = AuditEvent.objects.filter(organisation=scope.organisation).select_related(
        "campus"
    )
    action = request.GET.get("action", "")
    actor = request.GET.get("actor", "").strip()[:150]
    actions = sorted(
        set(
            AuditEvent.objects.filter(organisation=scope.organisation).values_list(
                "action", flat=True
            )
        )
    )
    if action in actions:
        events = events.filter(action=action)
    else:
        action = ""
    if actor:
        events = events.filter(actor_name__icontains=actor)
    page = Paginator(events, AUDIT_PAGE).get_page(request.GET.get("page"))
    return render(
        request,
        "tenancy/audit.html",
        {"page": page, "actions": actions, "action": action, "actor": actor},
    )
