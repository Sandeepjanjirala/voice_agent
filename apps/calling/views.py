"""Numbers, incoming calls, campaigns, instant leads and knowledge pages, and
the intake API that lead sources post to.

Every queryset comes from the request's scope (apps/tenancy/access.py). No view
here dials anyone: campaigns and instant leads run as dry runs only.
"""

import json
import logging
import os

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.crm import crypto
from apps.telephony import calls as telephony_calls
from apps.telephony.models import CallRecord
from apps.tenancy import audit
from apps.tenancy.access import scoped
from apps.workforce import knowledge
from apps.workforce.models import KnowledgeItem

from . import inbound, intake, queue
from .forms import (
    AddContactsForm,
    CampaignForm,
    ConsentAttestForm,
    KnowledgeForm,
    LeadSourceForm,
    PhoneNumberForm,
)
from .models import (
    Campaign,
    IntakeEvent,
    LeadSource,
    MissedCall,
    PhoneNumber,
    QueuedCall,
)

logger = logging.getLogger(__name__)

INTAKE_RATE_PER_MINUTE = 60
INTAKE_MAX_BYTES = 16_384
SECRET_SESSION_KEY = "lead_source_secret"


def _numbers(scope):
    return scope.limit_shared(
        PhoneNumber.objects.select_related("campus", "employee__live_version"),
        campuses_field="campus",
    )


def _campaigns(scope):
    return scope.limit(
        Campaign.objects.select_related("campus", "employee__live_version")
    )


def _sources(scope):
    return scope.limit(
        LeadSource.objects.select_related("campus", "employee__live_version")
    )


# --- numbers and incoming calls -----------------------------------------------------


def _diagnostics() -> list[dict]:
    """What stands between this installation and answering real phone calls.
    Read from configuration only; nothing here contacts LiveKit or the provider."""
    from apps.telephony.conf import TelephonyConfigError, load_telephony_settings
    from apps.telephony.planning import build_plan

    checks = [
        {
            "label": "The voice worker can report calls to Django",
            "done": telephony_calls.reporting_configured(),
            "help": "Set CALL_EVENTS_URL and CALL_EVENTS_SECRET (32+ characters).",
        },
        {
            "label": "The voice worker asks Django who answers each number",
            "done": bool(os.environ.get("INBOUND_CONTEXT_URL", "").strip()),
            "help": "Set INBOUND_CONTEXT_URL in .env.local and restart the worker.",
        },
        {
            "label": "Caller numbers can be matched to contacts",
            "done": crypto.configured(),
            "help": crypto.problem(),
        },
    ]
    try:
        plan = build_plan(
            load_telephony_settings(), agent_name=settings.LIVEKIT_AGENT_NAME
        )
    except TelephonyConfigError as error:
        checks.append({"label": "Inbound SIP trunk", "done": False, "help": str(error)})
    else:
        blockers = [blocker.message for blocker in plan.blockers]
        checks.append(
            {
                "label": "Inbound SIP trunk can be authenticated securely",
                "done": plan.ready,
                "help": " ".join(blockers[:3]),
            }
        )
    return checks


@scoped("numbers.view")
def numbers(request):
    scope = request.scope
    manage = scope.can("numbers.manage")
    form = PhoneNumberForm(request.POST or None, scope=scope)
    if request.method == "POST":
        if not manage:
            raise PermissionDenied
        if form.is_valid():
            number = form.save()
            audit.record(scope, "number.added", number, number=number.number)
            messages.success(request, f"{number.number} is added.")
            return redirect("numbers")
    rows = [
        {"number": number, "problems": inbound.problems(number)}
        for number in _numbers(scope)
    ]
    return render(
        request,
        "calling/numbers.html",
        {
            "rows": rows,
            "form": form,
            "manage": manage,
            "checks": _diagnostics(),
            "outbound": telephony_calls_readiness(),
        },
    )


def telephony_calls_readiness() -> dict:
    try:
        outbound, plan = telephony_calls.outbound_context()
    except telephony_calls.CallError as error:
        return {"ready": False, "reason": str(error), "number": ""}
    from apps.telephony.masking import mask_phone

    return {
        "ready": plan.ready,
        "reason": " ".join(b.message for b in plan.blockers),
        "number": mask_phone(outbound.allowed_number),
        "caller_id": mask_phone(outbound.caller_id),
    }


@scoped("numbers.manage")
def number_edit(request, pk):
    scope = request.scope
    number = scope.get(_numbers(scope), pk=pk)
    form = PhoneNumberForm(request.POST or None, instance=number, scope=scope)
    if request.method == "POST":
        if request.POST.get("action") == "delete":
            audit.record(scope, "number.removed", number=number.number)
            number.delete()
            messages.success(request, "The number was removed.")
            return redirect("numbers")
        if form.is_valid():
            number = form.save()
            audit.record(
                scope,
                "number.changed",
                number,
                routing=number.routing_enabled,
                employee=number.employee.name if number.employee else "",
            )
            messages.success(request, f"{number.number} is saved.")
            return redirect("numbers")
    return render(
        request,
        "calling/number.html",
        {"form": form, "number": number, "problems": inbound.problems(number)},
    )


@scoped("calls.view")
def incoming(request):
    scope = request.scope
    calls = scope.limit(
        CallRecord.objects.filter(
            direction=CallRecord.Direction.INBOUND
        ).select_related("campus", "contact", "employee_version__employee", "result")
    )
    missed = MissedCall.objects.filter(call__in=calls).select_related(
        "call__contact", "call__campus", "call__result", "handled_by"
    )
    can_handle = scope.can("missed.handle")
    if request.method == "POST":
        if not can_handle:
            raise PermissionDenied
        item = scope.get(missed, pk=request.POST.get("missed", ""))
        if item.handled_at is None:
            item.handled_at = timezone.now()
            item.handled_by = scope.user
            item.save(update_fields=["handled_at", "handled_by"])
            audit.record(scope, "missed.handled", item.call)
            messages.success(request, "Marked as called back.")
        return redirect(reverse("inbound") + "#missed")
    rows = [
        {"number": number, "problems": inbound.problems(number)}
        for number in _numbers(scope)
    ]
    week_ago = timezone.now() - timezone.timedelta(days=7)
    return render(
        request,
        "calling/incoming.html",
        {
            "rows": rows,
            "calls": calls[:30],
            "total": calls.count(),
            "routed": sum(1 for row in rows if not row["problems"]),
            "checks": _diagnostics(),
            "missed": missed.filter(handled_at__isnull=True)[:50],
            "handled": missed.filter(handled_at__gte=week_ago)[:10],
            "can_handle": can_handle,
        },
    )


# --- campaigns ---------------------------------------------------------------------


@scoped("campaigns.view")
def campaigns(request):
    scope = request.scope
    found = _campaigns(scope).annotate(size=Count("queue"))
    return render(
        request,
        "calling/campaigns.html",
        {"campaigns": found, "manage": scope.can("campaigns.manage")},
    )


@scoped("campaigns.manage")
def campaign_new(request):
    scope = request.scope
    form = CampaignForm(request.POST or None, scope=scope)
    if request.method == "POST" and form.is_valid():
        campaign = form.save(commit=False)
        campaign.created_by = scope.user
        campaign.save()
        audit.record(scope, "campaign.created", campaign, name=campaign.name)
        messages.success(request, "Campaign created. Add contacts next.")
        return redirect("campaign-detail", pk=campaign.pk)
    return render(
        request, "calling/campaign_form.html", {"form": form, "campaign": None}
    )


@scoped("campaigns.view")
def campaign_detail(request, pk):
    scope = request.scope
    campaign = scope.get(_campaigns(scope), pk=pk)
    manage = scope.can("campaigns.manage")
    add_form = AddContactsForm(campaign=campaign, prefix="add")
    consent_form = ConsentAttestForm(prefix="consent")
    edit_form = CampaignForm(instance=campaign, scope=scope, prefix="edit")
    if request.method == "POST":
        if not manage:
            raise PermissionDenied
        action = request.POST.get("action")
        if action == "edit":
            edit_form = CampaignForm(
                request.POST, instance=campaign, scope=scope, prefix="edit"
            )
            if edit_form.is_valid():
                edit_form.save()
                messages.success(request, "Campaign saved.")
                return redirect("campaign-detail", pk=campaign.pk)
        elif action == "add":
            add_form = AddContactsForm(request.POST, campaign=campaign, prefix="add")
            if add_form.is_valid():
                data = add_form.cleaned_data
                if data["source"] == "import":
                    found = data["batch"].contact_set.all()
                else:
                    found = queue.eligible_contacts(campaign.campus)
                added, existing = queue.add_contacts(campaign, found)
                audit.record(scope, "campaign.contacts_added", campaign, added=added)
                messages.success(
                    request,
                    f"{added} contacts added; {existing} were already in the campaign.",
                )
                return redirect("campaign-detail", pk=campaign.pk)
        elif action == "attest":
            consent_form = ConsentAttestForm(request.POST, prefix="consent")
            if consent_form.is_valid():
                campaign.consent_attested = True
                campaign.consent_note = consent_form.cleaned_data["note"]
                campaign.consent_by = scope.user
                campaign.consent_at = timezone.now()
                campaign.status = Campaign.Status.READY
                campaign.save()
                audit.record(scope, "campaign.consent_attested", campaign)
                messages.success(
                    request, "Recorded. The campaign is ready for a dry run."
                )
                return redirect("campaign-detail", pk=campaign.pk)
        elif action == "dry_run":
            if not campaign.consent_attested:
                messages.error(
                    request, "Record that these people agreed to be called first."
                )
            else:
                run = queue.dry_run(
                    campaign.queue.all(),
                    queue.Rules.for_campaign(campaign),
                    campaign.employee,
                    user=scope.user,
                    campaign=campaign,
                )
                audit.record(
                    scope,
                    "campaign.dry_run",
                    campaign,
                    **{key: run.summary[key] for key in ("would_call", "skipped")},
                )
                messages.success(
                    request,
                    f"Dry run done: {run.summary['would_call']} would be called, "
                    f"{run.summary['skipped']} skipped. Nobody was called.",
                )
            return redirect("campaign-detail", pk=campaign.pk)
        elif action == "archive":
            campaign.status = Campaign.Status.ARCHIVED
            campaign.save(update_fields=["status"])
            messages.success(request, "Campaign archived.")
            return redirect("campaigns")
        elif action == "remove":
            entry = scope.get(campaign.queue.all(), pk=request.POST.get("entry"))
            entry.delete()
            messages.success(request, "Removed from the campaign.")
            return redirect("campaign-detail", pk=campaign.pk)
    entries = campaign.queue.select_related("contact").order_by(
        "status", "due_at", "id"
    )
    counts = dict(campaign.queue.values_list("status").annotate(n=Count("id")))
    return render(
        request,
        "calling/campaign.html",
        {
            "campaign": campaign,
            "manage": manage,
            "entries": entries[:200],
            "counts": {
                label: counts.get(value, 0)
                for value, label in QueuedCall.Status.choices
            },
            "total": sum(counts.values()),
            "last_run": campaign.runs.first(),
            "employee_problem": queue.employee_problem(campaign.employee),
            "add_form": add_form,
            "consent_form": consent_form,
            "edit_form": edit_form,
            "dialling_enabled": settings.CONTACT_DIALING_ENABLED,
        },
    )


# --- instant leads -------------------------------------------------------------------


@scoped("sources.view")
def instant(request):
    scope = request.scope
    sources = _sources(scope).annotate(lead_count=Count("events"))
    waiting = scope.limit(
        QueuedCall.objects.filter(source__isnull=False).select_related(
            "contact", "source", "lead"
        )
    )
    return render(
        request,
        "calling/instant.html",
        {
            "sources": sources,
            "queue": waiting.order_by("-created_at")[:50],
            "events": IntakeEvent.objects.filter(source__in=sources).select_related(
                "source", "lead"
            )[:20],
            "manage": scope.can("sources.manage"),
        },
    )


@scoped("sources.manage")
def source_new(request):
    scope = request.scope
    form = LeadSourceForm(request.POST or None, scope=scope)
    if request.method == "POST" and form.is_valid():
        source = form.save(commit=False)
        secret, source.secret_hash, source.secret_hint = intake.new_secret()
        source.created_by = scope.user
        source.save()
        audit.record(scope, "source.created", source, name=source.name)
        # Shown once on the next page, then gone.
        request.session[SECRET_SESSION_KEY] = {
            "source": str(source.pk),
            "secret": secret,
        }
        return redirect("source-detail", pk=source.pk)
    return render(request, "calling/source_form.html", {"form": form})


@scoped("sources.view")
def source_detail(request, pk):
    scope = request.scope
    source = scope.get(_sources(scope), pk=pk)
    manage = scope.can("sources.manage")
    form = LeadSourceForm(instance=source, scope=scope)
    if request.method == "POST":
        if not manage:
            raise PermissionDenied
        action = request.POST.get("action")
        if action == "edit":
            form = LeadSourceForm(request.POST, instance=source, scope=scope)
            if form.is_valid():
                form.save()
                messages.success(request, "Lead source saved.")
                return redirect("source-detail", pk=source.pk)
        elif action == "new_secret":
            secret, source.secret_hash, source.secret_hint = intake.new_secret()
            source.save(update_fields=["secret_hash", "secret_hint"])
            audit.record(scope, "source.secret_replaced", source)
            request.session[SECRET_SESSION_KEY] = {
                "source": str(source.pk),
                "secret": secret,
            }
            messages.success(
                request, "A new secret was made. The old one no longer works."
            )
            return redirect("source-detail", pk=source.pk)
        elif action == "test":
            event, _ = intake.receive(
                source, {"phone": "+919000000000", "name": "Test lead", "dry_run": True}
            )
            messages.success(
                request, f"Test delivery: {event.get_result_display()}. {event.detail}"
            )
            return redirect("source-detail", pk=source.pk)
        elif action == "dry_run":
            run = queue.dry_run(
                source.queue.all(),
                queue.Rules.for_source(source),
                source.employee,
                user=scope.user,
                source=source,
            )
            messages.success(
                request,
                f"Dry run done: {run.summary['would_call']} would be called, "
                f"{run.summary['skipped']} skipped. Nobody was called.",
            )
            return redirect("source-detail", pk=source.pk)
    shown = request.session.get(SECRET_SESSION_KEY)
    secret = ""
    if shown and shown.get("source") == str(source.pk):
        secret = shown["secret"]
        del request.session[SECRET_SESSION_KEY]
    return render(
        request,
        "calling/source.html",
        {
            "source": source,
            "form": form,
            "manage": manage,
            "secret": secret,
            "endpoint": request.build_absolute_uri(
                reverse("lead-intake", args=[source.pk])
            ),
            "events": source.events.select_related("lead")[:30],
            "queue": source.queue.select_related("contact", "lead").order_by(
                "-created_at"
            )[:30],
            "last_run": source.runs.first(),
            "employee_problem": queue.employee_problem(source.employee),
        },
    )


@csrf_exempt  # Called by other systems; authenticated by the source's secret.
@never_cache
@require_POST
def lead_intake(request, pk):
    """Receive one new enquiry. JSON in, JSON out. See the source page for the format."""
    if len(request.body) > INTAKE_MAX_BYTES:
        return JsonResponse({"error": "Too large."}, status=413)
    key = f"lead-intake:{pk}:{timezone.now():%Y%m%d%H%M}"
    cache.add(key, 0, timeout=70)
    if cache.incr(key) > INTAKE_RATE_PER_MINUTE:
        return JsonResponse(
            {"error": "Too many requests. Try again shortly."}, status=429
        )
    source = (
        LeadSource.objects.select_related("organisation", "campus", "employee")
        .filter(pk=pk)
        .first()
    )
    try:
        intake.authenticate(source, request.META.get(intake.SECRET_HEADER, ""))
        data = json.loads(request.body or b"null")
        event, lead = intake.receive(source, data)
    except ValueError:
        return JsonResponse({"error": "Send a JSON object."}, status=400)
    except intake.IntakeRefusedError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    logger.info("lead intake", extra={"source": str(source.pk), "result": event.result})
    status = 201 if event.result == IntakeEvent.Result.ACCEPTED else 200
    return JsonResponse(
        {
            "result": event.result,
            "detail": event.detail,
            "lead": str(lead.pk) if lead else None,
        },
        status=status,
    )


# --- knowledge -----------------------------------------------------------------------


def _knowledge(scope):
    return scope.limit_shared(
        KnowledgeItem.objects.select_related("campus"), campuses_field="campus"
    )


@scoped("knowledge.view")
def knowledge_list(request):
    scope = request.scope
    manage = scope.can("knowledge.manage")
    form = KnowledgeForm(request.POST or None, scope=scope)
    if request.method == "POST":
        if not manage:
            raise PermissionDenied
        if form.is_valid():
            item = form.save(commit=False)
            item.created_by = scope.user
            item.save()
            audit.record(scope, "knowledge.added", item, title=item.title)
            messages.success(
                request, f"“{item.title}” is added. It is used from the next call."
            )
            return redirect("knowledge")
    campus = None
    chosen = request.GET.get("campus", "")
    if chosen:
        try:
            campus = scope.campuses().filter(pk=chosen).first()
        except (ValidationError, ValueError):  # a malformed id previews nothing
            campus = None
    preview = knowledge.compile_for(scope.organisation, campus)
    return render(
        request,
        "calling/knowledge.html",
        {
            "items": _knowledge(scope),
            "form": form,
            "manage": manage,
            "campuses": scope.campuses(),
            "campus": campus,
            "preview": preview,
            "preview_size": len(preview),
            "limit": knowledge.LIMIT,
        },
    )


@scoped("knowledge.manage")
def knowledge_edit(request, pk):
    scope = request.scope
    item = scope.get(_knowledge(scope), pk=pk)
    form = KnowledgeForm(request.POST or None, instance=item, scope=scope)
    if request.method == "POST":
        if request.POST.get("action") == "delete":
            audit.record(scope, "knowledge.removed", title=item.title)
            item.delete()
            messages.success(request, "Removed.")
            return redirect("knowledge")
        if form.is_valid():
            form.save()
            audit.record(scope, "knowledge.changed", item, title=item.title)
            messages.success(request, "Saved. It is used from the next call.")
            return redirect("knowledge")
    return render(request, "calling/knowledge_edit.html", {"form": form, "item": item})
