"""Make calls: type numbers or upload a list, check it, confirm, and the AI
employee calls everyone one after another (calllists.py)."""

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.telephony import calls
from apps.tenancy import audit
from apps.tenancy.access import scoped
from apps.workforce.models import Employee

from . import calllists, retries
from .models import CallList, CallListEntry


class CallListForm(forms.Form):
    speaker = forms.ChoiceField(label="Who speaks")
    campus = forms.ChoiceField(label="Location", required=False)
    purpose = forms.CharField(
        label="What the calls are about",
        max_length=160,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. Appointment reminder"}),
    )
    numbers = forms.CharField(
        label="Type the numbers",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "placeholder": "One per line, with a name if you like:\nAsha Rao, 98765 43210\n9876543211",
            }
        ),
    )
    upload = forms.FileField(
        label="…or upload a list",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx,.csv"}),
    )

    def __init__(self, scope, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = scope
        employees = scope.limit_shared(
            Employee.objects.filter(live_version__isnull=False, taking_calls=True)
        ).order_by("name")
        choices = [
            (str(e.pk), f"{e.name} ({e.role})" if e.role else e.name) for e in employees
        ]
        self.fields["speaker"].choices = choices
        campuses = list(scope.campuses().filter(is_active=True))
        self.fields["campus"].choices = (
            [] if scope.campus_ids is not None else [("", "Whole organisation")]
        ) + [(str(c.pk), c.name) for c in campuses]
        self.fields["campus"].required = scope.campus_ids is not None
        self.employees = {str(e.pk): e for e in employees}
        self.campuses = {str(c.pk): c for c in campuses}

    def clean(self):
        data = super().clean()
        if not (data.get("numbers") or "").strip() and not data.get("upload"):
            raise forms.ValidationError("Type at least one number, or upload a list.")
        employee = self.employees.get(data.get("speaker"))
        campus = self.campuses.get(data.get("campus"))
        if (
            employee is not None
            and campus is not None
            and employee.campuses.exists()
            and not employee.campuses.filter(pk=campus.pk).exists()
        ):
            self.add_error(
                "campus", "This AI employee is not assigned to this location."
            )
        return data


def _lists(scope):
    return (
        scope.limit(CallList.objects.all())
        if scope.campus_ids is not None
        else (CallList.objects.filter(organisation=scope.organisation))
    )


@scoped("calls.place")
def make_calls(request):
    scope = request.scope
    form = CallListForm(
        scope,
        request.POST or None,
        request.FILES or None,
        # "Make calls" on an employee's page chooses that employee.
        initial={"speaker": request.GET.get("speaker", "")},
    )
    if not form.fields["speaker"].choices:
        form = None
    elif request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        employee = form.employees[data["speaker"]]
        campus = form.campuses.get(data.get("campus") or "")
        try:
            parsed = calllists.parse(data.get("numbers") or "", data.get("upload"))
            call_list = calllists.create(
                scope,
                name=_list_name(data),
                purpose=data.get("purpose") or "",
                employee=employee,
                campus=campus,
                parsed=parsed,
            )
        except calllists.ListError as error:
            form.add_error(None, str(error))
        else:
            if parsed.duplicates:
                messages.info(
                    request,
                    f"{parsed.duplicates} number{'s were' if parsed.duplicates > 1 else ' was'} listed twice and kept once.",
                )
            return redirect("call-list", pk=call_list.pk)
    recent = list(_lists(scope).select_related("employee")[:15])
    for item in recent:
        item.tally = calllists.counts(item)
    return render(
        request,
        "calling/make_calls.html",
        {
            "form": form,
            "recent": recent,
            "max_rows": calllists.MAX_ROWS,
            "can_manage_employees": scope.can("employees.manage"),
        },
    )


def _list_name(data) -> str:
    when = timezone.localtime().strftime("%d %b, %H:%M")
    return (data.get("purpose") or "Calls")[:80] + f" · {when}"


@scoped("calls.place")
@transaction.atomic
def call_list(request, pk):
    scope = request.scope
    lists = _lists(scope)
    if request.method == "POST":
        # Serialize state changes with the queue's next-entry claim. Avoid
        # nullable joins here: PostgreSQL cannot lock their optional rows.
        lists = lists.select_for_update()
    else:
        lists = lists.select_related("employee", "campus")
    found = scope.get(lists, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        state = CallList.Status
        if action == "start" and found.status == state.READY:
            if request.POST.get("consent") != "yes":
                messages.error(
                    request, "Tick the box to confirm everyone agreed to be called."
                )
                return redirect("call-list", pk=found.pk)
            calllists.start(scope, found)
            messages.success(
                request, "Calling has started. Keep this page open to follow it."
            )
        elif action == "pause" and found.status == state.RUNNING:
            found.status, found.waiting = state.PAUSED, "Paused by you."
            found.save(update_fields=["status", "waiting", "updated_at"])
            audit.record(scope, "call_list.paused", found)
        elif action == "resume" and found.status == state.PAUSED:
            found.status, found.waiting = state.RUNNING, ""
            found.save(update_fields=["status", "waiting", "updated_at"])
            audit.record(scope, "call_list.resumed", found)
        elif action == "cancel" and found.status in (
            state.READY,
            state.RUNNING,
            state.PAUSED,
        ):
            found.status, found.waiting = state.CANCELLED, ""
            found.save(update_fields=["status", "waiting", "updated_at"])
            found.entries.filter(status=CallListEntry.Status.WAITING).update(
                status=CallListEntry.Status.SKIPPED, reason="The list was cancelled"
            )
            audit.record(scope, "call_list.cancelled", found)
        elif action in ("start", "pause", "resume", "cancel"):
            # A double click, browser retry or an older open tab should show
            # the current list, without repeating consent or reviving it.
            messages.info(
                request,
                "This list has already changed. Review its current status below.",
            )
        else:
            raise PermissionDenied
        return redirect("call-list", pk=found.pk)
    entries = list(found.entries.select_related("call"))
    for entry in entries:
        entry.retry = retries.entry_options(scope, entry)
    return render(
        request,
        "calling/call_list.html",
        {
            "call_list": found,
            "entries": entries,
            "tally": calllists.counts(found),
            "step_url": reverse("call-list-step", args=[found.pk]),
        },
    )


@require_POST
@scoped("calls.place")
def call_list_retry(request, pk, entry_pk):
    scope = request.scope
    found = scope.get(_lists(scope).select_related("employee", "campus"), pk=pk)
    entry = scope.get(found.entries.select_related("call"), pk=entry_pk)
    try:
        retry = retries.retry_entry(scope, entry)
    except calls.CallError as error:
        messages.error(request, str(error))
        return redirect("call-list", pk=found.pk)
    target = retries.target_url(retry)
    if target:
        messages.success(
            request, "Your retry is ready. The original call remains in your history."
        )
        return redirect(target)
    messages.info(request, "A retry is already being prepared. Refresh in a moment.")
    return redirect("call-list", pk=found.pk)


@require_POST
@scoped("calls.place")
def call_list_step(request, pk):
    """One step of calling, asked for by the open list page; returns progress."""
    scope = request.scope
    found = scope.get(_lists(scope), pk=pk)
    if calllists.needs_steps(found):
        found = calllists.advance(found)
    entries = [
        {
            "id": entry.pk,
            "status": entry.status,
            "label": entry.get_status_display(),
            "reason": entry.reason,
            "call_url": reverse("conversation-detail", args=[entry.call_id])
            if entry.call_id
            else "",
            "retry": retries.entry_options(scope, entry),
        }
        for entry in found.entries.all()
    ]
    return JsonResponse(
        {
            "status": found.status,
            "status_label": found.get_status_display(),
            "waiting": found.waiting,
            "tally": calllists.counts(found),
            "entries": entries,
        }
    )
