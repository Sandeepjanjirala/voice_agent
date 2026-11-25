"""Numbers, incoming calls and knowledge pages.

Every queryset comes from the request's scope (apps/tenancy/access.py). No view
here dials anyone.
"""

import logging
import os

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.crm import crypto
from apps.telephony import calls as telephony_calls
from apps.telephony.models import CallRecord
from apps.tenancy import audit
from apps.tenancy.access import scoped
from apps.workforce import knowledge
from apps.workforce.models import KnowledgeItem

from . import inbound
from .forms import KnowledgeForm, PhoneNumberForm
from .models import MissedCall, PhoneNumber

logger = logging.getLogger(__name__)


def _numbers(scope):
    return scope.limit_shared(
        PhoneNumber.objects.select_related("campus", "employee__live_version"),
        campuses_field="campus",
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
        "number": mask_phone(outbound.rehearsal_number),
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
