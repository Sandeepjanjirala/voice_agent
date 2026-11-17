"""Integrations & actions pages, running an action from a lead, and the run
history. Every queryset is scoped to the request's organisation."""

import uuid

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.crm.models import Lead
from apps.tenancy import audit
from apps.tenancy.access import scoped

from . import actions, webhooks
from .forms import FIELDS, ActionForm, IntegrationForm, run_form
from .models import ActionRun, ApprovedAction, Integration, WebhookDelivery

RUNS_PAGE = 30


def _integrations(scope):
    return Integration.objects.filter(organisation=scope.organisation)


def _actions(scope):
    return ApprovedAction.objects.filter(
        organisation=scope.organisation
    ).select_related("integration")


@scoped("integrations.view")
def overview(request):
    scope = request.scope
    return render(
        request,
        "integrations/overview.html",
        {
            "integrations": _integrations(scope),
            "actions": _actions(scope),
            "runs": ActionRun.objects.filter(organisation=scope.organisation)[:10],
            "manage": scope.can("integrations.manage"),
            "live": settings.INTEGRATIONS_LIVE_ENABLED,
            "kinds": Integration.Kind.choices,
        },
    )


@scoped("integrations.manage")
def integration_new(request):
    scope = request.scope
    form = IntegrationForm(
        request.POST or None, initial={"kind": request.GET.get("kind", "")}
    )
    if request.method == "POST" and form.is_valid():
        integration, shown = form.save(scope.organisation, scope.user)
        audit.record(scope, "integration.created", integration, kind=integration.kind)
        if shown:
            request.session["webhook_secret"] = {
                "id": str(integration.pk),
                "secret": shown,
            }
        messages.success(request, f"{integration.name} is added.")
        return redirect("integration-detail", pk=integration.pk)
    return render(
        request,
        "integrations/integration_form.html",
        {"form": form, "kind_fields": [(k, form.kind_fields(k)) for k in FIELDS]},
    )


@scoped("integrations.view")
def integration_detail(request, pk):
    scope = request.scope
    integration = scope.get(_integrations(scope), pk=pk)
    manage = scope.can("integrations.manage")
    form = IntegrationForm(instance=integration)
    if request.method == "POST":
        if not manage:
            raise PermissionDenied
        action = request.POST.get("action")
        if action == "save":
            form = IntegrationForm(request.POST, instance=integration)
            if form.is_valid():
                form.save(scope.organisation, scope.user)
                audit.record(scope, "integration.changed", integration)
                messages.success(request, "Saved.")
                return redirect("integration-detail", pk=integration.pk)
        elif action == "test_webhook" and integration.kind == Integration.Kind.WEBHOOK:
            delivery = WebhookDelivery.objects.create(
                integration=integration,
                event="test",
                payload={
                    "id": str(uuid.uuid4()),
                    "event": "test",
                    "created_at": timezone.now().isoformat(),
                    "organisation": str(scope.organisation.pk),
                    "data": {},
                },
            )
            result = webhooks.deliver(delivery.pk)
            messages.info(
                request,
                f"Test event: {result.get_status_display()}. {result.last_error}".strip(),
            )
            return redirect("integration-detail", pk=integration.pk)
        elif action == "retry":
            delivery = scope.get(
                integration.deliveries.all(), pk=request.POST.get("delivery")
            )
            if delivery.status == WebhookDelivery.Status.FAILED:
                delivery.status, delivery.attempts, delivery.next_attempt_at = (
                    WebhookDelivery.Status.PENDING,
                    0,
                    None,
                )
                delivery.save()
            webhooks.deliver(delivery.pk)
            messages.info(request, "Delivery tried again.")
            return redirect("integration-detail", pk=integration.pk)
        elif action == "delete":
            if integration.actions.exists():
                messages.error(
                    request, "Remove or change the actions that use it first."
                )
            else:
                audit.record(scope, "integration.removed", name=integration.name)
                integration.delete()
                messages.success(request, "Removed.")
                return redirect("integrations")
    shown = request.session.get("webhook_secret")
    secret = ""
    if shown and shown.get("id") == str(integration.pk):
        secret = shown["secret"]
        del request.session["webhook_secret"]
    return render(
        request,
        "integrations/integration.html",
        {
            "integration": integration,
            "form": form,
            "kind_fields": [(integration.kind, form.kind_fields(integration.kind))],
            "manage": manage,
            "secret": secret,
            "deliveries": integration.deliveries.all()[:30],
            "actions": integration.actions.all(),
            "live": settings.INTEGRATIONS_LIVE_ENABLED,
        },
    )


@scoped("integrations.manage")
def action_new(request):
    scope = request.scope
    form = ActionForm(
        request.POST or None,
        organisation=scope.organisation,
        initial={"kind": request.GET.get("kind", "")},
    )
    if request.method == "POST" and form.is_valid():
        action = form.save(commit=False)
        action.created_by = scope.user
        action.save()
        audit.record(scope, "action.created", action, kind=action.kind)
        messages.success(
            request,
            f"“{action.label}” is added. Switch it on for an employee to use it after calls.",
        )
        return redirect("action-detail", pk=action.pk)
    return render(
        request, "integrations/action_form.html", {"form": form, "action": None}
    )


@scoped("integrations.view")
def action_detail(request, pk):
    scope = request.scope
    action = scope.get(_actions(scope), pk=pk)
    manage = scope.can("integrations.manage")
    form = ActionForm(instance=action, organisation=scope.organisation)
    test = run_form(action)
    leads = scope.limit(Lead.objects.select_related("contact"))[:50]
    if request.method == "POST":
        if not manage:
            raise PermissionDenied
        kind = request.POST.get("form")
        if kind == "save":
            form = ActionForm(
                request.POST, instance=action, organisation=scope.organisation
            )
            if form.is_valid():
                form.save()
                audit.record(scope, "action.changed", action, enabled=action.is_enabled)
                messages.success(request, "Saved.")
                return redirect("action-detail", pk=action.pk)
        elif kind == "test":
            test = run_form(action, request.POST)
            lead = None
            if request.POST.get("lead"):
                lead = scope.get(
                    scope.limit(Lead.objects.all()), pk=request.POST["lead"]
                )
            if test.is_valid():
                inputs = {
                    k: v for k, v in test.cleaned_data.items() if v not in (None, "")
                }
                run = actions.run(
                    action,
                    inputs,
                    actions.Context(
                        organisation=scope.organisation,
                        trigger=ActionRun.Trigger.TEST,
                        actor=scope.user,
                        lead=lead,
                    ),
                )
                messages.info(
                    request, f"Test run: {run.get_status_display()}. {run.message}"
                )
                return redirect("action-detail", pk=action.pk)
        elif kind == "delete":
            audit.record(scope, "action.removed", label=action.label)
            action.delete()
            messages.success(request, "Removed. Past runs are kept.")
            return redirect("integrations")
    return render(
        request,
        "integrations/action.html",
        {
            "action": action,
            "form": form,
            "test": test,
            "leads": leads,
            "manage": manage,
            "runs": action.runs.select_related("lead", "call", "actor")[:20],
            "live": settings.INTEGRATIONS_LIVE_ENABLED,
        },
    )


@scoped("integrations.view")
def runs(request):
    scope = request.scope
    found = ActionRun.objects.filter(organisation=scope.organisation).select_related(
        "lead", "call", "actor"
    )
    status = request.GET.get("status", "")
    if status in ActionRun.Status.values:
        found = found.filter(status=status)
    if not scope.all_campuses:
        found = found.filter(lead__campus__in=scope.campus_ids)
    page = Paginator(found, RUNS_PAGE).get_page(request.GET.get("page"))
    return render(
        request,
        "integrations/runs.html",
        {
            "page": page,
            "status": status,
            "statuses": ActionRun.Status.choices,
            "querystring": f"status={status}" if status else "",
        },
    )


@scoped("actions.run")
@require_POST
def run_on_lead(request, pk, action_pk):
    """A staff member runs an approved action for a lead they may see."""
    scope = request.scope
    lead = scope.get(
        scope.limit(Lead.objects.select_related("contact", "campus")), pk=pk
    )
    action = scope.get(_actions(scope).filter(is_enabled=True), pk=action_pk)
    form = run_form(action, request.POST)
    if not form.is_valid():
        messages.error(
            request, " ".join(f"{k}: {' '.join(v)}" for k, v in form.errors.items())
        )
        return redirect("lead-detail", pk=lead.pk)
    inputs = {k: v for k, v in form.cleaned_data.items() if v not in (None, "")}
    run = actions.run(
        action,
        inputs,
        actions.Context(organisation=scope.organisation, actor=scope.user, lead=lead),
    )
    audit.record(scope, "action.run", lead, label=action.label, status=run.status)
    level = (
        messages.success if run.status == ActionRun.Status.SUCCEEDED else messages.info
    )
    if run.status in (ActionRun.Status.FAILED, ActionRun.Status.REFUSED):
        level = messages.error
    level(request, f"{action.label}: {run.get_status_display()}. {run.message}")
    return redirect("lead-detail", pk=lead.pk)
