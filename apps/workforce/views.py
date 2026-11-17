"""Dashboard home, the employee list, the Hire wizard and the draft editor."""

import logging
import statistics
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.crm import crypto
from apps.crm.models import FollowUp, Lead
from apps.telephony import calls
from apps.telephony.masking import mask_phone
from apps.telephony.models import CallRecord
from apps.tenancy.access import scope_for, scoped

from . import llm, publishing
from .config import ACTIONS, LIMITS, MAX_QUESTIONS, MAX_STEPS, normalize
from .drafting import EXAMPLES, draft_employee
from .forms import BriefForm, EmployeeForm
from .models import Employee, EmployeeVersion
from .voices import PACE_CHOICES, VOICES

logger = logging.getLogger(__name__)

DRAFT_THROTTLE_SECONDS = 10
CALL_THROTTLE_SECONDS = 5
HOME_CALLS = 5
HOME_EMPLOYEES = 4
VERSIONS_SHOWN = 5


def _employees(scope):
    """The organisation's employees this user may see."""
    return scope.limit_shared(Employee.objects.all())


def _employee(request, pk) -> Employee:
    return get_object_or_404(_employees(request.scope), pk=pk)


def _outbound_ready() -> bool:
    try:
        _, plan = calls.outbound_context()
    except calls.CallError:
        return False
    return plan.ready


@never_cache
@login_required
def home(request):
    scope = scope_for(request)
    if scope is None:
        if request.user.is_superuser:
            return redirect("organisation-setup")
        # Everyone else has one page: the voice studio.
        return redirect("voice")
    now = timezone.now()
    calls_seen = scope.limit(CallRecord.objects.all())
    team = _employees(scope)
    week = calls_seen.filter(created_at__gte=now - timedelta(days=7))
    week_total = week.count()
    answered = week.filter(answered_at__isnull=False).count()
    latencies = list(
        week.exclude(e2e_latency_p50=None).values_list("e2e_latency_p50", flat=True)
    )
    day_start = timezone.localtime(now).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    employees = team.count()
    published = team.filter(live_version__isnull=False).count()
    open_leads = scope.limit(Lead.objects.filter(stage__in=Lead.OPEN_STAGES)).count()
    tasks_due = scope.limit(
        FollowUp.objects.filter(done_at__isnull=True, due_at__lte=now)
    ).count()
    setup = [
        {
            "label": "Contact numbers can be stored encrypted",
            "done": crypto.configured(),
            "help": "Run: python manage.py contactkeys generate",
        },
        {
            "label": "Voice service connected",
            "done": bool(settings.LIVEKIT_URL and settings.LIVEKIT_API_KEY),
            "help": "Set the LiveKit keys in .env.local.",
        },
        {
            "label": "Outbound test line ready",
            "done": _outbound_ready(),
            "help": "The platform's Phone calls page shows what is missing.",
            "url_name": "calls" if request.user.is_staff else "",
        },
        {
            "label": "Call results reported by the voice worker",
            "done": calls.reporting_configured(),
            "help": "Set CALL_EVENTS_URL and CALL_EVENTS_SECRET.",
        },
        {
            "label": "First AI employee drafted",
            "done": employees > 0,
            "help": "Describe a job in a sentence or two.",
            "url_name": "employee-hire",
        },
        {
            "label": "An employee published",
            "done": published > 0,
            "help": "Open an employee, try it, then publish.",
            "url_name": "employees",
        },
    ]
    return render(
        request,
        "workforce/home.html",
        {
            "stats": {
                "employees": employees,
                "published": published,
                "open_leads": open_leads,
                "tasks_due": tasks_due,
                "calls_today": calls_seen.filter(created_at__gte=day_start).count(),
                "week_total": week_total,
                "answer_rate": round(100 * answered / week_total)
                if week_total
                else None,
                "response": round(statistics.median(latencies), 1)
                if latencies
                else None,
            },
            "setup": setup,
            "setup_done": sum(item["done"] for item in setup),
            "team": team[:HOME_EMPLOYEES],
            "recent": [
                calls.serialize(record)
                for record in calls_seen.select_related("placed_by")[:HOME_CALLS]
            ],
        },
    )


@scoped("employees.view")
def employees(request):
    query = request.GET.get("q", "").strip()[:80]
    channel = request.GET.get("channel", "")
    status = request.GET.get("status", "")
    found = _employees(request.scope)
    if status == "draft":
        found = found.filter(live_version__isnull=True)
    elif status == "published":
        found = found.filter(live_version__isnull=False, taking_calls=True)
    elif status == "paused":
        found = found.filter(live_version__isnull=False, taking_calls=False)
    else:
        status = ""
    if query:
        found = found.filter(Q(name__icontains=query) | Q(role__icontains=query))
    if channel in Employee.Channel.values:
        found = found.filter(channel=channel)
    else:
        channel = ""
    return render(
        request,
        "workforce/employees.html",
        {
            "employees": found,
            "total": _employees(request.scope).count(),
            "query": query,
            "channel": channel,
            "status": status,
            "statuses": (
                ("draft", "Draft"),
                ("published", "Published"),
                ("paused", "Paused"),
            ),
            "channels": Employee.Channel.choices,
        },
    )


def _create_from_brief(request, form) -> Employee | None:
    """Draft and save an employee, or add a form error and return None."""
    if not cache.add(
        f"employee-draft:{request.user.pk}", True, timeout=DRAFT_THROTTLE_SECONDS
    ):
        form.add_error(None, "Please wait a few seconds before drafting again.")
        return None
    data = form.cleaned_data
    draft = draft_employee(data["brief"], data["channel"], data["language"])
    employee = Employee.objects.create(
        organisation=request.scope.organisation,
        name=draft.config["agent_name"] or "New employee",
        role=draft.config["role"] or "AI calling assistant",
        channel=data["channel"],
        language=data["language"],
        brief=data["brief"],
        draft=draft.config,
        draft_source=draft.source,
        created_by=request.user,
    )
    logger.info(
        "employee drafted",
        extra={"employee_id": str(employee.pk), "draft_source": draft.source},
    )
    if draft.notice:
        messages.warning(request, draft.notice)
    return employee


@scoped("employees.manage")
def hire(request):
    form = BriefForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        employee = _create_from_brief(request, form)
        if employee is not None:
            messages.success(
                request,
                f"{employee.name} is drafted. Read it through and change "
                "anything: nothing is used on a call yet.",
            )
            return redirect("employee-detail", pk=employee.pk)
    return render(
        request,
        "workforce/hire.html",
        {"form": form, "examples": EXAMPLES, "llm_ready": llm.configured()},
    )


def _uuids(post, name: str) -> list:
    """Posted values that are UUIDs; anything else is ignored."""
    found = []
    for value in post.getlist(name):
        try:
            found.append(uuid.UUID(value))
        except ValueError:
            continue
    return found


def _posted_config(post, current: dict) -> dict:
    """The editor's fields as a configuration. Steps and questions are repeated
    inputs, in the order shown on the page."""
    names = post.getlist("step_name")
    guidance = post.getlist("step_guidance")
    return normalize(
        {
            **current,
            "agent_name": post.get("name", ""),
            "role": post.get("role", ""),
            "greeting": post.get("greeting", ""),
            "purpose": post.get("purpose", ""),
            "limits": post.get("limits", ""),
            "ending": post.get("ending", ""),
            "strictness": post.get("strictness", ""),
            "steps": [
                {"name": name, "guidance": text}
                for name, text in zip(names, guidance, strict=False)
            ],
            "questions": post.getlist("question"),
            "voice": {
                "speaker": post.get("voice_speaker", ""),
                "pace": post.get("voice_pace", ""),
            },
            "actions": post.getlist("action"),
        }
    )


@scoped("employees.manage")
def employee_detail(request, pk):
    employee = _employee(request, pk)
    form = EmployeeForm(request.POST or None, instance=employee)
    if request.method == "POST" and form.is_valid():
        employee = form.save(commit=False)
        employee.draft = _posted_config(request.POST, employee.draft)
        employee.save()
        # Only campuses of this organisation can be chosen, whatever was posted.
        employee.campuses.set(
            request.scope.campuses().filter(pk__in=_uuids(request.POST, "campus"))
        )
        messages.success(request, "Draft saved.")
        return redirect("employee-detail", pk=employee.pk)
    # After a rejected save, show what was typed rather than what is stored.
    config = (
        _posted_config(request.POST, employee.draft)
        if request.method == "POST"
        else normalize(employee.draft)
    )
    return render(
        request,
        "workforce/employee.html",
        {
            "employee": employee,
            "form": form,
            "config": config,
            "limits": LIMITS,
            "max_steps": MAX_STEPS,
            "max_questions": MAX_QUESTIONS,
            "strictness_levels": range(1, 6),
            "voices": VOICES,
            "paces": PACE_CHOICES,
            "actions": ACTIONS,
            "test_line": _test_line(),
            "campuses": request.scope.campuses().filter(is_active=True),
            "assigned": set(employee.campuses.values_list("pk", flat=True)),
            # About the saved draft, not about unsaved typing.
            "problems": publishing.problems(employee.draft),
            "prompt_size": publishing.prompt_size(employee.draft),
            "prompt_budget": publishing.PROMPT_BYTES,
            "has_changes": publishing.has_changes(employee),
            "versions": employee.versions.select_related("published_by")[
                :VERSIONS_SHOWN
            ],
        },
    )


def _test_line() -> dict:
    """Whether a test call can be placed, and to which (masked) number."""
    try:
        outbound, plan = calls.outbound_context()
    except calls.CallError as error:
        return {"ready": False, "reason": str(error), "number": ""}
    reason = "" if plan.ready else " ".join(b.message for b in plan.blockers)
    return {
        "ready": plan.ready,
        "reason": reason,
        "number": mask_phone(outbound.allowed_number),
    }


@scoped("employees.manage")
@require_POST
def employee_test_call(request, pk):
    """Place one real call to the approved test number, using the employee's
    published version. Nothing else can be dialled from here."""
    employee = _employee(request, pk)
    assigned = list(employee.campuses.all()[:2])
    # A call belongs to a campus only when the employee has just one.
    campus = assigned[0] if len(assigned) == 1 else None
    try:
        plan = publishing.plan_call(employee, campus=campus)
    except publishing.PublishError as error:
        messages.error(request, "No call was placed. " + " ".join(error.reasons))
        return redirect("employee-detail", pk=employee.pk)
    version = plan.version
    if not cache.add(
        f"outbound-call:{request.user.pk}", True, timeout=CALL_THROTTLE_SECONDS
    ):
        messages.error(request, "Please wait a few seconds before calling again.")
        return redirect("employee-detail", pk=employee.pk)
    try:
        record = calls.start_call(user=request.user, call_plan=plan, campus=campus)
    except calls.CallError as error:
        logger.warning("employee test call not started: %s", error)
        messages.error(
            request, " ".join(["No call was placed.", str(error), *error.details])
        )
        return redirect("employee-detail", pk=employee.pk)
    logger.info(
        "employee test call started",
        extra={"call_id": str(record.pk), "employee_version": version.number},
    )
    messages.success(
        request,
        f"Calling the test number with {employee.name}, version {version.number}.",
    )
    return redirect("conversation-detail", pk=record.pk)


@scoped("employees.manage")
@require_POST
def employee_publish(request, pk):
    """Make the saved draft the version that calls use."""
    employee = _employee(request, pk)
    try:
        version = publishing.publish(employee, request.user)
    except publishing.PublishError as error:
        messages.error(request, "Not published. " + " ".join(error.reasons))
    else:
        logger.info(
            "employee published",
            extra={"employee_id": str(employee.pk), "version": version.number},
        )
        messages.success(
            request, f"Version {version.number} of {employee.name} is published."
        )
    return redirect("employee-detail", pk=employee.pk)


@scoped("employees.manage")
@require_POST
def employee_discard(request, pk):
    employee = _employee(request, pk)
    try:
        publishing.discard_changes(employee)
    except publishing.PublishError as error:
        messages.error(request, " ".join(error.reasons))
    else:
        messages.success(
            request, "Changes discarded. The draft matches what is published."
        )
    return redirect("employee-detail", pk=employee.pk)


@scoped("employees.manage")
@require_POST
def employee_restore(request, pk, number):
    """Copy an earlier version into the draft. Nothing changes on calls until
    it is published."""
    employee = _employee(request, pk)
    version = get_object_or_404(EmployeeVersion, employee=employee, number=number)
    publishing.copy_into_draft(employee, version)
    messages.success(
        request,
        f"Version {version.number} is copied into the draft. Publish to use it.",
    )
    return redirect("employee-detail", pk=employee.pk)


@scoped("employees.manage")
@require_POST
def employee_switch(request, pk):
    """Pause or resume a published employee."""
    employee = _employee(request, pk)
    if employee.live_version_id is None:
        messages.error(request, "Publish the employee before switching it on.")
    else:
        employee.taking_calls = request.POST.get("taking_calls") == "on"
        employee.save(update_fields=["taking_calls", "updated_at"])
        messages.success(
            request,
            f"{employee.name} is taking calls."
            if employee.taking_calls
            else f"{employee.name} is paused.",
        )
    return redirect("employee-detail", pk=employee.pk)


@scoped("employees.manage")
@require_POST
def employee_redraft(request, pk):
    """Replace the draft with a fresh one made from the original brief."""
    employee = _employee(request, pk)
    if not cache.add(
        f"employee-draft:{request.user.pk}", True, timeout=DRAFT_THROTTLE_SECONDS
    ):
        messages.error(request, "Please wait a few seconds before drafting again.")
        return redirect("employee-detail", pk=employee.pk)
    draft = draft_employee(employee.brief, employee.channel, employee.language)
    employee.draft = draft.config
    employee.draft_source = draft.source
    employee.name = draft.config["agent_name"] or employee.name
    employee.role = draft.config["role"] or employee.role
    employee.save()
    if draft.notice:
        messages.warning(request, draft.notice)
    else:
        messages.success(request, "Drafted again from your description.")
    return redirect("employee-detail", pk=employee.pk)


@scoped("employees.manage")
@require_POST
def employee_delete(request, pk):
    employee = _employee(request, pk)
    name = employee.name
    employee.delete()
    logger.info("employee deleted", extra={"employee_id": str(pk)})
    messages.success(request, f"{name} was deleted.")
    return redirect("employees")
