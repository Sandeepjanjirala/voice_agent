"""Leads, contacts, follow-ups, conversations, performance and privacy settings.

Every queryset comes from the request's scope (apps/tenancy/access.py), so a
record in another organisation or campus is a 404 here, whatever ID is typed.
"""

import csv
import re
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.calling import analysis, recordings, retries, review
from apps.calling.models import CallResult, CallTranscript
from apps.telephony import calls as telephony_calls
from apps.telephony.models import CallRecord
from apps.tenancy import audit
from apps.tenancy.access import scoped
from apps.tenancy.forms import OrganisationForm, RetentionForm
from apps.workforce import publishing
from apps.workforce.models import Employee

from . import contacts, crypto, importing, reports, retention
from .forms import (
    ConsentForm,
    ContactForm,
    FollowUpForm,
    ImportForm,
    LeadNewForm,
    LeadUpdateForm,
    OptOutForm,
)
from .models import Contact, FollowUp, Lead, LeadNote, OptOut
from .phones import InvalidPhoneError, normalize

PAGE = 25
EXPORT_LIMIT = 5000


def _leads(scope):
    return scope.limit(
        Lead.objects.select_related("contact", "campus", "assigned_to", "employee")
    )


def _contacts(scope):
    return scope.limit(Contact.objects.select_related("campus"))


def _calls(scope):
    return scope.limit(
        CallRecord.objects.select_related(
            "campus",
            "placed_by",
            "lead",
            "contact",
            "employee_version__employee",
            "result",
        )
    )


def _campus_filter(request, scope):
    """The campus chosen in a filter, if it is one the user may see."""
    chosen = request.GET.get("campus", "")
    if not chosen:
        return None
    try:
        return scope.campuses().filter(pk=chosen).first()
    except (ValidationError, ValueError):  # a malformed id filters nothing
        return None


def _page(request, queryset):
    return Paginator(queryset, PAGE).get_page(request.GET.get("page"))


def _query(request, *drop) -> str:
    """The current filters as a query string, for paging and export links."""
    params = request.GET.copy()
    for name in ("page", *drop):
        params.pop(name, None)
    return params.urlencode()


def _number_lookup(text: str) -> str | None:
    """The lookup value if the search text is a phone number, else None."""
    try:
        return crypto.lookup(normalize(text))
    except (InvalidPhoneError, crypto.CryptoError):
        return None


def _csv_cell(value):
    """Stop a spreadsheet from running a cell as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


# --- leads -------------------------------------------------------------------------


@scoped("leads.view")
def leads(request):
    scope = request.scope
    everything = _leads(scope)
    found = everything
    stage = request.GET.get("stage", "")
    assigned = request.GET.get("assigned", "")
    query = request.GET.get("q", "").strip()[:80]
    campus = _campus_filter(request, scope)
    if stage in Lead.Stage.values:
        found = found.filter(stage=stage)
    elif stage == "open":
        found = found.filter(stage__in=Lead.OPEN_STAGES)
    else:
        stage = ""
    if campus:
        found = found.filter(campus=campus)
    if assigned == "me":
        found = found.filter(assigned_to=scope.user)
    elif assigned == "none":
        found = found.filter(assigned_to__isnull=True)
    else:
        assigned = ""
    if query:
        lookup = _number_lookup(query)
        if lookup:
            found = found.filter(contact__phone_lookup=lookup)
        else:
            found = found.filter(
                Q(contact__name__icontains=query) | Q(subject__icontains=query)
            )
    counts = dict(everything.values_list("stage").annotate(n=Count("id")))
    return render(
        request,
        "crm/leads.html",
        {
            "page": _page(request, found),
            "stages": [
                {"value": value, "label": label, "count": counts.get(value, 0)}
                for value, label in Lead.Stage.choices
            ],
            "total": sum(counts.values()),
            "stage": stage,
            "assigned": assigned,
            "query": query,
            "campus": campus,
            "campuses": scope.campuses(),
            "querystring": _query(request),
            "tasks_due": _open_tasks(scope).filter(due_at__lte=timezone.now()).count(),
        },
    )


@scoped("leads.edit")
def lead_new(request):
    scope = request.scope
    form = LeadNewForm(request.POST or None, scope=scope)
    problem = crypto.problem()
    if request.method == "POST" and not problem and form.is_valid():
        data = form.cleaned_data
        existing = contacts.find(scope.organisation, data["phone"])
        contact = None
        if existing is None:
            contact = contacts.create(
                scope,
                data["campus"],
                name=data["name"],
                phone=data["phone"],
                origin=data["origin"],
                calling_purpose=data["calling_purpose"],
                consent=data["consent"],
                consent_evidence=data["consent_evidence"],
            )
        elif existing.is_erased or not _contacts(scope).filter(pk=existing.pk).exists():
            # Do not reveal anything about a contact this user cannot see.
            form.add_error(
                "phone",
                "This number already belongs to a contact in another location. "
                "Ask an organisation admin.",
            )
        else:
            contact = existing
            messages.info(
                request,
                f"This number already belongs to {contact.name}; "
                "the lead was added to that contact.",
            )
        if contact is not None:
            lead = Lead.objects.create(
                organisation=scope.organisation,
                campus=contact.campus,
                contact=contact,
                subject=data["subject"],
                details=data["details"],
                assigned_to=data["assigned_to"],
                employee=data["employee"],
                created_by=scope.user,
            )
            audit.record(scope, "lead.created", lead)
            messages.success(request, "Lead added.")
            return redirect("lead-detail", pk=lead.pk)
    return render(request, "crm/lead_new.html", {"form": form, "problem": problem})


@scoped("leads.view")
def lead_detail(request, pk):
    scope = request.scope
    lead = scope.get(_leads(scope), pk=pk)
    update = LeadUpdateForm(instance=lead, scope=scope)
    task = FollowUpForm(scope=scope, campus=lead.campus, prefix="task")
    if request.method == "POST":
        if not scope.can("leads.edit"):
            raise PermissionDenied
        action = request.POST.get("action")
        if action == "update":
            before = (lead.stage, lead.assigned_to_id)
            update = LeadUpdateForm(request.POST, instance=lead, scope=scope)
            if update.is_valid():
                lead = update.save(commit=False)
                if lead.is_open:
                    lead.closed_at = None
                else:
                    lead.closed_at = lead.closed_at or timezone.now()
                lead.save()
                if before[1] != lead.assigned_to_id:
                    audit.record(
                        scope,
                        "lead.assigned",
                        lead,
                        to=lead.assigned_to.get_username() if lead.assigned_to else "",
                    )
                if before[0] != lead.stage:
                    audit.record(scope, "lead.stage", lead, stage=lead.stage)
                messages.success(request, "Lead updated.")
                return redirect("lead-detail", pk=lead.pk)
        elif action == "note":
            text = request.POST.get("text", "").strip()[:2000]
            if text:
                LeadNote.objects.create(lead=lead, author=scope.user, text=text)
                messages.success(request, "Note added.")
            return redirect("lead-detail", pk=lead.pk)
        elif action == "task":
            task = FollowUpForm(
                request.POST, scope=scope, campus=lead.campus, prefix="task"
            )
            if task.is_valid():
                follow_up = task.save(commit=False)
                follow_up.lead = lead
                follow_up.organisation = lead.organisation
                follow_up.campus = lead.campus
                follow_up.created_by = scope.user
                follow_up.save()
                messages.success(request, "Follow-up added.")
                return redirect("lead-detail", pk=lead.pk)
        elif action == "task_done":
            done = scope.get(
                lead.follow_ups.filter(done_at__isnull=True),
                pk=request.POST.get("task"),
            )
            done.done_at, done.done_by = timezone.now(), scope.user
            done.save(update_fields=["done_at", "done_by"])
            messages.success(request, "Follow-up done.")
            return redirect("lead-detail", pk=lead.pk)
    rehearsal_employees = (
        scope.limit_shared(Employee.objects.all())
        .filter(live_version__isnull=False, taking_calls=True)
        .select_related("live_version")
        if scope.can("employees.manage")
        else Employee.objects.none()
    )
    return render(
        request,
        "crm/lead.html",
        {
            "lead": lead,
            "contact": lead.contact,
            "call_block": contacts.call_block(lead.contact),
            "update": update,
            "task": task,
            "notes": lead.notes.select_related("author"),
            "tasks": lead.follow_ups.select_related("assigned_to"),
            "calls": _calls(scope).filter(lead=lead)[:20],
            "can_edit": scope.can("leads.edit"),
            "rehearsal_employees": rehearsal_employees,
            "test_number": _test_number(),
            "contact_dialing": settings.CONTACT_DIALING_ENABLED,
        },
    )


def _open_tasks(scope):
    return scope.limit(
        FollowUp.objects.filter(done_at__isnull=True).select_related(
            "lead__contact", "campus", "assigned_to"
        )
    )


@scoped("leads.view")
def followups(request):
    scope = request.scope
    found = _open_tasks(scope)
    who = request.GET.get("who", "me")
    if who == "me":
        found = found.filter(assigned_to=scope.user)
    elif who == "none":
        found = found.filter(assigned_to__isnull=True)
    else:
        who = "all"
    if request.method == "POST":
        if not scope.can("leads.edit"):
            raise PermissionDenied
        done = scope.get(_open_tasks(scope), pk=request.POST.get("task"))
        done.done_at, done.done_by = timezone.now(), scope.user
        done.save(update_fields=["done_at", "done_by"])
        messages.success(request, "Follow-up done.")
        return redirect(f"{request.path}?who={who}")
    return render(
        request,
        "crm/followups.html",
        {"page": _page(request, found), "who": who, "now": timezone.now()},
    )


# --- contacts ----------------------------------------------------------------------


@scoped("contacts.view")
def contact_list(request):
    scope = request.scope
    found = _contacts(scope).filter(erased_at__isnull=True)
    consent = request.GET.get("consent", "")
    query = request.GET.get("q", "").strip()[:80]
    campus = _campus_filter(request, scope)
    if consent in Contact.Consent.values:
        found = found.filter(consent=consent)
    else:
        consent = ""
    if campus:
        found = found.filter(campus=campus)
    if query:
        lookup = _number_lookup(query)
        found = (
            found.filter(phone_lookup=lookup)
            if lookup
            else found.filter(name__icontains=query)
        )
    page = _page(request, found)
    opted_out = set(
        OptOut.objects.filter(
            organisation=scope.organisation,
            phone_lookup__in=[contact.phone_lookup for contact in page],
        ).values_list("phone_lookup", flat=True)
    )
    for contact in page:
        contact.opted_out = contact.phone_lookup in opted_out
    return render(
        request,
        "crm/contacts.html",
        {
            "page": page,
            "consent": consent,
            "consents": Contact.Consent.choices,
            "query": query,
            "campus": campus,
            "campuses": scope.campuses(),
            "querystring": _query(request),
            "can_export": scope.can("contacts.export"),
        },
    )


@scoped("contacts.export")
def contact_export(request):
    """Contacts as CSV with their numbers. Each export is logged."""
    scope = request.scope
    found = _contacts(scope).filter(erased_at__isnull=True)[:EXPORT_LIMIT]
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="contacts.csv"'
    writer = csv.writer(response)
    writer.writerow(
        ["Name", "Number", "Location", "Origin", "Permission to call", "Added"]
    )
    rows = 0
    for contact in found:
        writer.writerow(
            _csv_cell(value)
            for value in (
                contact.name,
                contact.display_phone,
                contact.campus.name,
                contact.get_origin_display(),
                contact.get_consent_display(),
                f"{contact.created_at:%Y-%m-%d}",
            )
        )
        rows += 1
    audit.record(scope, "contacts.exported", rows=rows)
    return response


@scoped("contacts.view")
def contact_detail(request, pk):
    scope = request.scope
    contact = scope.get(_contacts(scope), pk=pk)
    can_edit = scope.can("contacts.edit") and not contact.is_erased
    details = ContactForm(instance=contact)
    consent = ConsentForm(
        initial={
            "consent": contact.consent,
            "consent_evidence": contact.consent_evidence,
        }
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "erase":
            if not scope.can("contacts.erase"):
                raise PermissionDenied
            contacts.erase(contact)
            audit.record(scope, "contact.erased", contact)
            messages.success(request, "The contact's personal details were erased.")
            return redirect("contacts")
        if not can_edit:
            raise PermissionDenied
        if action == "details":
            details = ContactForm(request.POST, instance=contact)
            if details.is_valid():
                details.save()
                audit.record(scope, "contact.changed", contact)
                messages.success(request, "Contact saved.")
                return redirect("contact-detail", pk=contact.pk)
        elif action == "consent":
            consent = ConsentForm(request.POST)
            if consent.is_valid():
                contacts.record_consent(
                    scope,
                    contact,
                    consent.cleaned_data["consent"],
                    consent.cleaned_data["consent_evidence"],
                )
                messages.success(request, "Permission to call recorded.")
                return redirect("contact-detail", pk=contact.pk)
        elif action == "opt_out":
            contacts.opt_out(scope, contact, request.POST.get("reason", "")[:200])
            messages.success(request, "Added to the do-not-call list.")
            return redirect("contact-detail", pk=contact.pk)
        elif action == "whatsapp":
            agreed = request.POST.get("whatsapp_consent") == "on"
            evidence = " ".join(request.POST.get("whatsapp_evidence", "").split())[:300]
            if agreed and not evidence:
                messages.error(
                    request, "Say how and when they agreed to WhatsApp messages."
                )
                return redirect("contact-detail", pk=contact.pk)
            contact.whatsapp_consent = agreed
            contact.whatsapp_consent_evidence = evidence if agreed else ""
            contact.whatsapp_consent_at = timezone.now()
            contact.save(
                update_fields=[
                    "whatsapp_consent",
                    "whatsapp_consent_evidence",
                    "whatsapp_consent_at",
                    "updated_at",
                ]
            )
            audit.record(scope, "contact.whatsapp_consent", contact, agreed=agreed)
            messages.success(request, "WhatsApp permission recorded.")
            return redirect("contact-detail", pk=contact.pk)
    else:
        audit.record(scope, "contact.viewed", contact)
    return render(
        request,
        "crm/contact.html",
        {
            "contact": contact,
            "details": details,
            "consent": consent,
            "can_edit": can_edit,
            "can_erase": scope.can("contacts.erase") and not contact.is_erased,
            "call_block": contacts.call_block(contact),
            "opted_out": contacts.is_opted_out(
                scope.organisation, contact.phone_lookup
            ),
            "leads": _leads(scope).filter(contact=contact),
        },
    )


@scoped("contacts.import")
def contact_import(request):
    scope = request.scope
    form = ImportForm(request.POST or None, request.FILES or None, scope=scope)
    problem = crypto.problem()
    rows = counts = None
    if request.method == "POST" and not problem and form.is_valid():
        data = form.cleaned_data
        try:
            if request.POST.get("action") == "import":
                batch = importing.run(
                    scope,
                    data["campus"],
                    data["text"],
                    origin=data["origin"],
                    calling_purpose=data["calling_purpose"],
                    consent_attested=data["consent_attested"],
                    consent_evidence=data["consent_evidence"],
                    create_leads=data["create_leads"],
                    default_subject=data["default_subject"],
                )
                messages.success(
                    request,
                    f"{batch.created} contacts added. {batch.duplicates} duplicates, "
                    f"{batch.invalid} invalid and {batch.opted_out} on the "
                    "do-not-call list were left out.",
                )
                return redirect("contacts")
            rows = importing.analyse(scope.organisation, data["text"])
            counts = importing.summary(rows)
            # A chosen file is not sent again by the browser, so the checked
            # list goes back into the text box for the second step.
            posted = request.POST.copy()
            posted["text"] = data["text"]
            form = ImportForm(posted, scope=scope)
            form.is_valid()
        except importing.ImportFormatError as error:
            form.add_error("text", str(error))
    return render(
        request,
        "crm/import.html",
        {
            "form": form,
            "problem": problem,
            "rows": rows,
            "counts": counts,
        },
    )


# --- conversations -------------------------------------------------------------------


def _filtered_calls(request, scope):
    found = _calls(scope)
    status = request.GET.get("status", "")
    direction = request.GET.get("direction", "")
    days = request.GET.get("days", "")
    outcome = request.GET.get("outcome", "")
    employee = request.GET.get("employee", "")
    campus = _campus_filter(request, scope)
    if outcome in CallResult.Outcome.values:
        found = found.filter(result__outcome=outcome)
    else:
        outcome = ""
    employees = scope.limit_shared(Employee.objects.all())
    chosen = None
    if employee:
        try:
            chosen = employees.filter(pk=employee).first()
        except (ValidationError, ValueError):
            chosen = None
    if chosen is not None:
        found = found.filter(employee_version__employee=chosen)
    if status in CallRecord.Status.values:
        found = found.filter(status=status)
    else:
        status = ""
    if direction in CallRecord.Direction.values:
        found = found.filter(direction=direction)
    else:
        direction = ""
    if days in ("1", "7", "30", "90"):
        found = found.filter(created_at__gte=timezone.now() - timedelta(days=int(days)))
    else:
        days = ""
    if campus:
        found = found.filter(campus=campus)
    # What staff must look at: calls marked for follow-up, or with problems.
    attention = request.GET.get("attention", "")
    if attention == "follow_up":
        found = found.filter(result__follow_up_needed=True)
    elif attention == "issues":
        found = found.filter(result__has_issues=True)
    else:
        attention = ""
    # The employees' own result labels, as they appear on calls in scope.
    labels = sorted(
        set(
            _calls(scope)
            .exclude(result__result_label="")
            .exclude(result__isnull=True)
            .values_list("result__result_label", flat=True)
        )
    )
    label = request.GET.get("result", "")
    if label in labels:
        found = found.filter(result__result_label=label)
    else:
        label = ""
    return found, {
        "status": status,
        "direction": direction,
        "days": days,
        "campus": campus,
        "outcome": outcome,
        "employee": chosen,
        "attention": attention,
        "result": label,
        "labels": labels,
    }


@scoped("calls.view")
def conversations(request):
    scope = request.scope
    found, filters = _filtered_calls(request, scope)
    page = _page(request, found)
    for call in page:
        call.retry = retries.call_options(scope, call)
    return render(
        request,
        "crm/conversations.html",
        {
            "page": page,
            "summary": reports.call_summary(found),
            "filters": filters,
            "statuses": CallRecord.Status.choices,
            "directions": CallRecord.Direction.choices,
            "outcomes": CallResult.Outcome.choices,
            "employees": scope.limit_shared(Employee.objects.all()),
            "campuses": scope.campuses(),
            "querystring": _query(request),
            "can_export": scope.can("calls.export"),
        },
    )


@scoped("calls.export")
def conversations_export(request):
    """The filtered calls as CSV with their numbers. Each export is logged."""
    scope = request.scope
    found, _ = _filtered_calls(request, scope)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="conversations.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "Started",
            "Direction",
            "Number",
            "Result",
            "Talk seconds",
            "Location",
            "AI employee",
            "Response seconds (median)",
        ]
    )
    rows = 0
    for call in found[:EXPORT_LIMIT]:
        version = call.employee_version
        writer.writerow(
            _csv_cell(value)
            for value in (
                f"{timezone.localtime(call.created_at):%Y-%m-%d %H:%M}",
                call.get_direction_display(),
                call.display_number,
                call.get_status_display(),
                call.duration_seconds,
                call.campus.name if call.campus else "",
                f"{version.employee.name} v{version.number}" if version else "",
                call.e2e_latency_p50,
            )
        )
        rows += 1
    audit.record(scope, "calls.exported", rows=rows)
    return response


@scoped("calls.view")
def conversation_detail(request, pk):
    scope = request.scope
    call = scope.get(_calls(scope), pk=pk)
    may_end = scope.can("employees.manage") or call.placed_by_id == scope.user.pk
    if request.method == "POST" and request.POST.get("action") == "hang_up":
        if not may_end:
            raise PermissionDenied
        try:
            telephony_calls.hang_up(call)
        except telephony_calls.CallError as error:
            messages.error(request, str(error))
        else:
            audit.record(scope, "call.ended", call)
            messages.success(request, "The call was ended.")
        return redirect("conversation-detail", pk=call.pk)
    if request.method == "POST" and request.POST.get("action") == "analyse":
        if not scope.can("calls.analyse"):
            raise PermissionDenied
        if call.is_active:
            messages.error(request, "The call is still in progress.")
        else:
            result = analysis.analyse(call)
            audit.record(scope, "call.analysed", call, status=result.status)
            if result.status == CallResult.Status.DONE:
                messages.success(request, "The call was analysed.")
            else:
                messages.error(
                    request, result.error or "The call could not be analysed."
                )
        return redirect("conversation-detail", pk=call.pk)
    if request.method == "POST" and request.POST.get("action") == "create_lead":
        if not scope.can("leads.edit"):
            raise PermissionDenied
        return _lead_from_caller(request, scope, call)
    if request.method == "POST":
        call.notes = request.POST.get("notes", "").strip()[:2000]
        call.save(update_fields=["notes", "updated_at"])
        audit.record(scope, "call.noted", call)
        messages.success(request, "Notes saved.")
        return redirect("conversation-detail", pk=call.pk)
    if call.is_active:
        # Someone is watching: bring the status up to date from LiveKit.
        call = telephony_calls.refresh(call)
    result = CallResult.objects.filter(call=call).first()
    transcript = CallTranscript.objects.filter(call=call).first()
    questions = {
        question["key"]: question["label"]
        for question in call.context_snapshot.get("questions") or []
    }
    answers = [
        (questions.get(key, key), value)
        for key, value in (result.answers.items() if result else ())
    ]
    profile = call.context_snapshot.get("profile") or {}
    turns = [
        {**turn, "at": review.clock(turn.get("t"))}
        for turn in (transcript.turns if transcript else [])
    ]
    if transcript is not None:
        # Reading what a person said on a call is personal data: logged.
        audit.record(scope, "call.transcript_viewed", call, turns=len(turns))
    issues = result.issues if result else []
    return render(
        request,
        "crm/conversation.html",
        {
            "call": call,
            "may_end": may_end,
            "can_manage_employee": scope.can("employees.manage"),
            "retry": retries.call_options(scope, call),
            "result": result,
            "answers": answers,
            "transcript": transcript,
            "turns": turns,
            "transcript_note": _transcript_note(call, transcript, result),
            "measured_issues": [i for i in issues if i.get("source") == "data"],
            "ai_issues": [i for i in issues if i.get("source") != "data"],
            "rec": _recording_view(request, scope, call),
            "actions": call.actions.all(),
            "missed": getattr(call, "missed", None),
            "snapshot": call.context_snapshot,
            "voice": profile.get("voice") or {},
            "can_analyse": scope.can("calls.analyse") and not call.is_active,
            "can_create_lead": scope.can("leads.edit")
            and bool(call.remote_encrypted)
            and call.contact_id is None
            and call.campus_id is not None,
        },
    )


@require_POST
@scoped("calls.place")
def conversation_retry(request, pk):
    scope = request.scope
    call = scope.get(_calls(scope), pk=pk)
    try:
        retry = retries.retry_call(scope, call)
    except telephony_calls.CallError as error:
        messages.error(request, str(error))
        return redirect("conversation-detail", pk=call.pk)
    target = retries.target_url(retry)
    if target:
        messages.success(
            request, "Your retry is ready. The original call remains in your history."
        )
        return redirect(target)
    messages.info(request, "A retry is already being prepared. Refresh in a moment.")
    return redirect("conversation-detail", pk=call.pk)


def _recording_view(request, scope, call) -> dict:
    """The Recording section: the player and its links when the person may
    listen, otherwise the actual reason there is nothing to play."""
    from apps.calling.models import CallRecording

    recording = CallRecording.objects.filter(call=call).first()
    if recording is not None:
        recordings.settle(recording)
    view = {"recording": recording, "can_listen": scope.can("calls.recordings")}
    decision = (call.context_snapshot or {}).get("recording")
    if recording is not None and recording.status != CallRecording.Status.AVAILABLE:
        note = recording.reason or recording.get_status_display()
    elif recording is not None:
        note = ""
    elif call.is_active:
        note = "Nothing to play while the call is in progress."
    elif decision is None:
        note = "Not recorded: call recording was not set up when this call was made."
    elif not decision.get("allowed"):
        note = f"Not recorded. {decision.get('reason', '')}".strip()
    elif call.answered_at is None:
        note = "Not recorded: nobody answered."
    elif decision.get("authorization") == "demo":
        note = "The demo recording did not arrive from the voice worker."
    else:
        note = (
            "Not recorded: the person was not asked, or did not answer, the "
            "recording question."
        )
    view["note"] = note
    if recording is not None and recording.playable:
        if not view["can_listen"]:
            view["note"] = "You do not have permission to listen to call recordings."
        else:
            base = reverse("conversation-recording", args=[call.pk])
            view["audio_url"] = (
                f"{base}?t={recordings.link_token(recording, scope.user)}"
            )
            if scope.can("calls.recordings_download"):
                token = recordings.link_token(recording, scope.user, download=True)
                view["download_url"] = f"{base}?download=1&t={token}"
            # Opening a playable recording is an access, logged like a play.
            audit.record(scope, "recording.accessed", call)
    return view


@scoped("calls.recordings")
def conversation_recording(request, pk):
    """The audio of a call, for the person the link was made for, for ten
    minutes. Streamed by Django (with byte ranges for seeking); the stored
    file and its storage are never exposed."""
    from apps.calling.models import CallRecording

    scope = request.scope
    call = scope.get(_calls(scope), pk=pk)
    recording = CallRecording.objects.filter(call=call).first()
    download = request.GET.get("download") == "1"
    if recording is None or not recording.playable:
        raise Http404
    if download and not scope.can("calls.recordings_download"):
        raise PermissionDenied
    if not recordings.token_valid(
        request.GET.get("t", ""), recording, scope.user, download=download
    ):
        return HttpResponse("This link has expired. Reload the call page.", status=403)
    try:
        data = recordings.read(recording)
    except recordings.RecordingError as error:
        return HttpResponse(f"The recording cannot be played: {error}.", status=503)
    if download:
        audit.record(scope, "recording.downloaded", call)
    return _ranged_audio(request, data, call, download)


def _ranged_audio(request, data: bytes, call, download: bool) -> HttpResponse:
    total = len(data)
    start, end, status_code = 0, total - 1, 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", request.headers.get("Range", "").strip())
    if match and (match.group(1) or match.group(2)):
        if match.group(1):
            start = int(match.group(1))
            end = min(int(match.group(2)), total - 1) if match.group(2) else total - 1
        else:
            start = max(total - int(match.group(2)), 0)
        if start > end or start >= total:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{total}"
            return response
        status_code = 206
    response = HttpResponse(
        data[start : end + 1], status=status_code, content_type="audio/ogg"
    )
    if status_code == 206:
        response["Content-Range"] = f"bytes {start}-{end}/{total}"
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    name = f"call-{timezone.localtime(call.created_at):%Y%m%d-%H%M}.ogg"
    response["Content-Disposition"] = (
        f'{"attachment" if download else "inline"}; filename="{name}"'
    )
    return response


def _transcript_note(call, transcript, result) -> str:
    """Why the conversation is or is not shown, in the organisation's terms,
    so nothing disappears without the page saying so."""
    organisation = call.organisation
    mode = organisation.transcripts if organisation else "off"
    if transcript is not None:
        if mode == "keep":
            days = retention.policy_for(organisation).transcript_days
            return (
                f"Kept for {days} days under your organisation's retention policy."
                if days
                else "Kept with the call under your organisation's setting."
            )
        return "Kept until the summary is written, then deleted, as your organisation's setting says."
    if result is not None and result.transcript_deleted_at:
        return (
            f"The conversation was deleted on {timezone.localtime(result.transcript_deleted_at):%d %b %Y at %H:%M} "
            "after the summary was written, as your organisation's setting says "
            '("Use it for the call summary, then delete it"). An admin can choose to '
            "keep conversations under Privacy & settings."
        )
    if call.is_active:
        return "The conversation is shown here when the call ends."
    if call.answered_at is None:
        return "Nobody answered, so there was no conversation."
    if mode == "off":
        return (
            "Your organisation does not keep what was said on calls, so there is no "
            "conversation or summary. An admin can change this under Privacy & settings."
        )
    if call.employee_version_id is None:
        return (
            "This call used the Agent profile, which does not report the conversation."
        )
    return (
        "The voice worker did not report the conversation for this call "
        "(CALL_EVENTS_URL and CALL_EVENTS_SECRET must be set for the worker)."
    )


def _lead_from_caller(request, scope, call):
    """Turn an unknown inbound caller into a contact and lead. The number is
    copied from the call record's encrypted copy; nobody sees it."""
    name = " ".join(request.POST.get("name", "").split())[:120]
    subject = " ".join(request.POST.get("subject", "").split())[:160]
    if not (name and subject):
        messages.error(request, "Give the caller's name and what they called about.")
        return redirect("conversation-detail", pk=call.pk)
    if not call.remote_encrypted or call.contact_id or call.campus is None:
        messages.error(request, "This caller cannot be turned into a lead.")
        return redirect("conversation-detail", pk=call.pk)
    try:
        number = crypto.decrypt(call.remote_encrypted)
        contact = contacts.create(
            scope,
            call.campus,
            name=name,
            phone=number,
            origin=Contact.Origin.PHONE,
            origin_detail="Called the business",
            calling_purpose=subject,
        )
    except contacts.DuplicateContactError:
        messages.error(request, "This caller is already a contact.")
        return redirect("conversation-detail", pk=call.pk)
    except crypto.CryptoError:
        messages.error(request, "The caller's number could not be read.")
        return redirect("conversation-detail", pk=call.pk)
    except InvalidPhoneError:
        messages.error(
            request, "The caller rang from a landline; only mobiles can be stored."
        )
        return redirect("conversation-detail", pk=call.pk)
    lead = Lead.objects.create(
        organisation=scope.organisation,
        campus=call.campus,
        contact=contact,
        subject=subject,
        created_by=scope.user,
        employee=call.employee_version.employee if call.employee_version else None,
    )
    CallRecord.objects.filter(pk=call.pk).update(contact=contact, lead=lead)
    audit.record(scope, "lead.created", lead, from_call=str(call.pk))
    messages.success(
        request,
        "Lead added. Permission to call them back is not recorded yet: "
        "record it on the contact if they agreed.",
    )
    return redirect("lead-detail", pk=lead.pk)


# --- performance ---------------------------------------------------------------------


@scoped("reports.view")
def performance(request):
    scope = request.scope
    try:
        days = int(request.GET.get("days", 30))
    except ValueError:
        days = 30
    if days not in reports.RANGES:
        days = 30
    since = timezone.now() - timedelta(days=days)
    campus = _campus_filter(request, scope)
    calls = scope.limit(CallRecord.objects.filter(created_at__gte=since))
    lead_set = scope.limit(Lead.objects.filter(created_at__gte=since))
    if campus:
        calls, lead_set = calls.filter(campus=campus), lead_set.filter(campus=campus)
    employees = scope.limit_shared(Employee.objects.all())
    campus_rows = reports.by_campus(
        [campus] if campus else scope.campuses(), calls, lead_set
    )
    employee_rows = reports.by_employee(employees, calls, lead_set)
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="performance.csv"'
        writer = csv.writer(response)
        writer.writerow(
            ["Group", "Name", "Leads", "Converted", "Calls", "Answered", "Talk min"]
        )
        for group, rows in (("Location", campus_rows), ("AI employee", employee_rows)):
            for row in rows:
                writer.writerow(
                    _csv_cell(value)
                    for value in (
                        group,
                        row["name"],
                        row["leads"],
                        row["won"],
                        row["total"],
                        row["answered"],
                        row["talk_minutes"],
                    )
                )
        audit.record(scope, "report.exported", days=days)
        return response
    return render(
        request,
        "crm/performance.html",
        {
            "days": days,
            "ranges": reports.RANGES,
            "campus": campus,
            "campuses": scope.campuses(),
            "summary": reports.call_summary(calls),
            "lead_total": lead_set.count(),
            "lead_won": lead_set.filter(stage=Lead.Stage.WON).count(),
            "by_day": reports.calls_by_day(calls, min(days, 30)),
            "by_status": reports.calls_by_status(calls),
            "by_stage": reports.leads_by_stage(lead_set),
            "campus_rows": campus_rows,
            "employee_rows": employee_rows,
            "querystring": _query(request, "format"),
        },
    )


# --- privacy and settings ---------------------------------------------------------------


@scoped()
def privacy_settings(request):
    scope = request.scope
    manage = scope.can("organisation.manage")
    organisation_form = OrganisationForm(instance=scope.organisation, prefix="org")
    policy = retention.policy_for(scope.organisation)
    retention_form = RetentionForm(instance=policy, prefix="keep")
    opt_out_form = OptOutForm(prefix="dnc")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "opt_out":
            opt_out_form = OptOutForm(request.POST, prefix="dnc")
            if crypto.problem():
                opt_out_form.add_error(None, crypto.problem())
            elif opt_out_form.is_valid():
                entry = contacts.opt_out_number(
                    scope.organisation,
                    opt_out_form.cleaned_data["phone"],
                    reason=opt_out_form.cleaned_data["reason"],
                    user=scope.user,
                )
                audit.record(scope, "optout.added", entry)
                messages.success(
                    request, f"{entry.display_phone} is on the do-not-call list."
                )
                return redirect("settings")
        elif not manage:
            raise PermissionDenied
        elif action == "organisation":
            organisation_form = OrganisationForm(
                request.POST, instance=scope.organisation, prefix="org"
            )
            if organisation_form.is_valid():
                organisation_form.save()
                messages.success(request, "Settings saved.")
                return redirect("settings")
        elif action == "retention":
            retention_form = RetentionForm(request.POST, instance=policy, prefix="keep")
            if retention_form.is_valid():
                retention_form.save()
                audit.record(scope, "retention.changed", **retention_form.cleaned_data)
                messages.success(request, "Retention policy saved.")
                return redirect("settings")
        elif action == "retention_apply":
            counts = retention.apply(scope.organisation, actor=scope.user)
            messages.success(
                request,
                f"Retention applied: {counts['contacts']} contacts erased, "
                f"{counts['calls']} call records, {counts['transcripts']} "
                f"transcripts, {counts['recordings']} recordings and {counts['audit']} "
                "access-log entries deleted.",
            )
            return redirect("settings")
    return render(
        request,
        "crm/settings.html",
        {
            "manage": manage,
            "organisation_form": organisation_form,
            "retention_form": retention_form,
            "retention_preview": retention.preview(scope.organisation),
            "opt_out_form": opt_out_form,
            "opt_outs": OptOut.objects.filter(organisation=scope.organisation)[:200],
            "can_remove": scope.can("optout.remove"),
            "crypto_problem": crypto.problem(),
        },
    )


@scoped("optout.remove")
@require_POST
def opt_out_remove(request, pk):
    scope = request.scope
    entry = scope.get(OptOut.objects.filter(organisation=scope.organisation), pk=pk)
    masked = entry.phone_masked
    shown = entry.display_phone
    entry.delete()
    audit.record(scope, "optout.removed", number=masked)
    messages.success(request, f"{shown} was taken off the do-not-call list.")
    return redirect("settings")


def _test_number() -> str:
    try:
        outbound, plan = telephony_calls.outbound_context()
    except telephony_calls.CallError:
        return ""
    return outbound.rehearsal_number if plan.ready else ""


@scoped("employees.manage")
@require_POST
def lead_call(request, pk):
    """Follow up using the lead's own permitted number, never a test substitute."""
    from apps.calling.outbound import contact_call

    scope = request.scope
    lead = scope.get(_leads(scope), pk=pk)
    employee = scope.get(
        scope.limit_shared(Employee.objects.all()), pk=request.POST.get("employee")
    )
    if not cache.add(f"outbound-call:{scope.user.pk}", True, timeout=5):
        messages.error(request, "Please wait a few seconds before calling again.")
        return redirect("lead-detail", pk=lead.pk)
    try:
        record = contact_call(
            contact=lead.contact, employee=employee, user=scope.user, lead=lead
        )
    except telephony_calls.CallError as error:
        messages.error(request, "No call was placed. " + str(error))
        return redirect("lead-detail", pk=lead.pk)
    audit.record(scope, "lead.outbound_call", lead, call=str(record.pk))
    return redirect("conversation-detail", pk=record.pk)


@scoped("employees.manage")
@require_POST
def lead_rehearse(request, pk):
    """Ring the approved test number with a published employee, as if it were
    calling this lead's contact. The contact is never dialled."""
    scope = request.scope
    lead = scope.get(_leads(scope), pk=pk)
    employee = scope.get(
        scope.limit_shared(Employee.objects.all()), pk=request.POST.get("employee")
    )
    try:
        plan = publishing.plan_call(
            employee, campus=lead.campus, lead=lead, rehearsal=True
        )
    except publishing.PublishError as error:
        messages.error(request, "No call was placed. " + " ".join(error.reasons))
        return redirect("lead-detail", pk=lead.pk)
    if not cache.add(f"outbound-call:{scope.user.pk}", True, timeout=5):
        messages.error(request, "Please wait a few seconds before calling again.")
        return redirect("lead-detail", pk=lead.pk)
    try:
        record = telephony_calls.start_call(
            user=scope.user, call_plan=plan, campus=lead.campus, lead=lead
        )
    except telephony_calls.CallError as error:
        messages.error(
            request, " ".join(["No call was placed.", str(error), *error.details])
        )
        return redirect("lead-detail", pk=lead.pk)
    audit.record(scope, "lead.rehearsal_call", lead, call=str(record.pk))
    messages.success(
        request,
        f"Calling the test number with {employee.name}, version "
        f"{plan.version.number}, as if calling {lead.contact.name}.",
    )
    return redirect("conversation-detail", pk=record.pk)
