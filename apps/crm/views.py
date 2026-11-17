"""Leads, contacts, follow-ups, conversations, performance and privacy settings.

Every queryset comes from the request's scope (apps/tenancy/access.py), so a
record in another organisation or campus is a 404 here, whatever ID is typed.
"""

import csv
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.telephony import calls as telephony_calls
from apps.telephony.models import CallRecord
from apps.tenancy import audit
from apps.tenancy.access import scoped
from apps.tenancy.forms import OrganisationForm, RetentionForm
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
            "campus", "placed_by", "lead", "employee_version__employee"
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
                "This number already belongs to a contact in another campus. "
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
    """Contacts as CSV, with masked numbers only. Each export is logged."""
    scope = request.scope
    found = _contacts(scope).filter(erased_at__isnull=True)[:EXPORT_LIMIT]
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="contacts.csv"'
    writer = csv.writer(response)
    writer.writerow(
        ["Name", "Number (masked)", "Campus", "Origin", "Permission to call", "Added"]
    )
    rows = 0
    for contact in found:
        writer.writerow(
            _csv_cell(value)
            for value in (
                contact.name,
                contact.phone_masked,
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
    campus = _campus_filter(request, scope)
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
    return found, {
        "status": status,
        "direction": direction,
        "days": days,
        "campus": campus,
    }


@scoped("calls.view")
def conversations(request):
    scope = request.scope
    found, filters = _filtered_calls(request, scope)
    return render(
        request,
        "crm/conversations.html",
        {
            "page": _page(request, found),
            "summary": reports.call_summary(found),
            "filters": filters,
            "statuses": CallRecord.Status.choices,
            "directions": CallRecord.Direction.choices,
            "campuses": scope.campuses(),
            "querystring": _query(request),
            "can_export": scope.can("calls.export"),
        },
    )


@scoped("calls.export")
def conversations_export(request):
    """The filtered calls as CSV, with masked numbers. Each export is logged."""
    scope = request.scope
    found, _ = _filtered_calls(request, scope)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="conversations.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "Started",
            "Direction",
            "Number (masked)",
            "Result",
            "Talk seconds",
            "Campus",
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
                call.masked_number,
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
    if request.method == "POST":
        call.notes = request.POST.get("notes", "").strip()[:2000]
        call.save(update_fields=["notes", "updated_at"])
        audit.record(scope, "call.noted", call)
        messages.success(request, "Notes saved.")
        return redirect("conversation-detail", pk=call.pk)
    if call.is_active:
        # Someone is watching: bring the status up to date from LiveKit.
        call = telephony_calls.refresh(call)
    return render(request, "crm/conversation.html", {"call": call, "may_end": may_end})


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
        for group, rows in (("Campus", campus_rows), ("AI employee", employee_rows)):
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
                    request, f"{entry.phone_masked} is on the do-not-call list."
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
                messages.success(request, "Organisation saved.")
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
                f"{counts['calls']} call records and {counts['audit']} "
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
    entry.delete()
    audit.record(scope, "optout.removed", number=masked)
    messages.success(request, f"{masked} was taken off the do-not-call list.")
    return redirect("settings")
