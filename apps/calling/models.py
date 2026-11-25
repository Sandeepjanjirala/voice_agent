"""Calling channels and what calls produce.

Numbers route inbound calls to a published employee. Campaigns and instant-lead
sources put contacts on a queue that a dry run works through: it decides who
would be called, when, and who must be skipped and why, without dialling.
Results hold the AI summary of a call and every action the system took after it.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

USER = settings.AUTH_USER_MODEL


class PhoneNumber(models.Model):
    """A business line of the organisation and which employee answers it.
    The number is the organisation's own, not a person's, so it is stored as is."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        "tenancy.Organisation", on_delete=models.CASCADE, related_name="numbers"
    )
    campus = models.ForeignKey(
        "tenancy.Campus",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="numbers",
    )
    number = models.CharField(max_length=16, unique=True)
    label = models.CharField(max_length=80, blank=True)
    employee = models.ForeignKey(
        "workforce.Employee",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="numbers",
    )
    # Owner's switch: off means calls to this number are not given an employee.
    routing_enabled = models.BooleanField(default=False)
    # Where a caller would be transferred when they ask for a person. Stored for
    # the transfer design; no call is transferred yet (see docs/INBOUND.md).
    transfer_to = models.CharField(max_length=16, blank=True)
    # Outbound: the number may be shown as the caller ID on business calls of
    # its campus (all campuses when it has none). Both must be on: the admin's
    # choice, and the provider's confirmation that the number may be presented.
    outbound_enabled = models.BooleanField(default=False)
    outbound_verified = models.BooleanField(default=False)
    # The LiveKit outbound trunk for this caller ID; blank uses the main trunk,
    # which must list this number (or "*") among its numbers.
    outbound_trunk_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("number",)

    def __str__(self) -> str:
        return self.label or self.number

    @property
    def is_caller_id(self) -> bool:
        return self.outbound_enabled and self.outbound_verified


class Campaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        READY = "ready", "Ready (dry run only)"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        "tenancy.Organisation", on_delete=models.CASCADE, related_name="campaigns"
    )
    campus = models.ForeignKey(
        "tenancy.Campus", on_delete=models.PROTECT, related_name="campaigns"
    )
    employee = models.ForeignKey(
        "workforce.Employee",
        null=True,
        on_delete=models.SET_NULL,
        related_name="campaigns",
    )
    name = models.CharField(max_length=120)
    purpose = models.CharField(
        max_length=200, help_text="What these people are being called about."
    )
    status = models.CharField(
        max_length=8, choices=Status.choices, default=Status.DRAFT
    )
    window_start = models.TimeField(default="10:00")
    window_end = models.TimeField(default="18:00")
    # Weekdays, Monday = 0.
    days = models.JSONField(default=list)
    max_attempts = models.PositiveSmallIntegerField(default=2)
    retry_after_minutes = models.PositiveIntegerField(default=120)
    daily_limit = models.PositiveIntegerField(default=100)
    # Skip anyone called by another campaign within this many days.
    cooldown_days = models.PositiveIntegerField(default=7)
    consent_attested = models.BooleanField(default=False)
    consent_note = models.TextField(max_length=1000, blank=True)
    consent_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    consent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.name


class LeadSource(models.Model):
    """A private address that a website form or another system posts new
    enquiries to. Each new lead is queued for a call by the source's employee."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        "tenancy.Organisation", on_delete=models.CASCADE, related_name="lead_sources"
    )
    campus = models.ForeignKey(
        "tenancy.Campus", on_delete=models.PROTECT, related_name="lead_sources"
    )
    employee = models.ForeignKey(
        "workforce.Employee",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lead_sources",
    )
    name = models.CharField(max_length=120)
    # Shown to the person on the form; stored as the evidence of their consent.
    consent_statement = models.TextField(max_length=1000)
    # SHA-256 of the secret. The secret itself is shown once and never stored.
    secret_hash = models.CharField(max_length=64)
    secret_hint = models.CharField(max_length=4)
    is_active = models.BooleanField(default=True)
    call_delay_minutes = models.PositiveIntegerField(default=5)
    window_start = models.TimeField(default="09:00")
    window_end = models.TimeField(default="20:00")
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class IntakeEvent(models.Model):
    """One delivery to a lead source, kept so problems can be traced."""

    class Result(models.TextChoices):
        ACCEPTED = "accepted", "Accepted"
        DUPLICATE = "duplicate", "Already received"
        OPTED_OUT = "opted_out", "On the do-not-call list"
        INVALID = "invalid", "Rejected"
        TEST = "test", "Test (nothing stored)"

    source = models.ForeignKey(
        LeadSource, on_delete=models.CASCADE, related_name="events"
    )
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    result = models.CharField(max_length=10, choices=Result.choices)
    detail = models.CharField(max_length=200, blank=True)
    masked_number = models.CharField(max_length=24, blank=True)
    lead = models.ForeignKey(
        "crm.Lead", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ("-received_at",)


class QueuedCall(models.Model):
    """A contact waiting to be called by a campaign or after an instant lead."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Waiting"
        WOULD_CALL = "would_call", "Would be called"
        SKIPPED = "skipped", "Skipped"
        DONE = "done", "Done"

    organisation = models.ForeignKey(
        "tenancy.Organisation", on_delete=models.CASCADE, related_name="+"
    )
    campus = models.ForeignKey("tenancy.Campus", on_delete=models.PROTECT)
    contact = models.ForeignKey(
        "crm.Contact", on_delete=models.CASCADE, related_name="queued_calls"
    )
    lead = models.ForeignKey(
        "crm.Lead", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    campaign = models.ForeignKey(
        Campaign, null=True, blank=True, on_delete=models.CASCADE, related_name="queue"
    )
    source = models.ForeignKey(
        LeadSource,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="queue",
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    reason = models.CharField(max_length=200, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    due_at = models.DateTimeField(null=True, blank=True)
    # When the last dry run decided this entry.
    checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("due_at", "created_at")
        constraints = (
            models.UniqueConstraint(
                fields=["campaign", "contact"],
                condition=Q(campaign__isnull=False),
                name="one_entry_per_contact_per_campaign",
            ),
        )


class DryRun(models.Model):
    """What a dry run decided, kept as a report. Nothing was dialled."""

    organisation = models.ForeignKey(
        "tenancy.Organisation", on_delete=models.CASCADE, related_name="+"
    )
    campaign = models.ForeignKey(
        Campaign, null=True, blank=True, on_delete=models.CASCADE, related_name="runs"
    )
    source = models.ForeignKey(
        LeadSource, null=True, blank=True, on_delete=models.CASCADE, related_name="runs"
    )
    run_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    run_at = models.DateTimeField(auto_now_add=True)
    summary = models.JSONField(default=dict)

    class Meta:
        ordering = ("-run_at",)


class CallTranscript(models.Model):
    """What was said, when the organisation chooses to keep it. Numbers masked."""

    call = models.OneToOneField(
        "telephony.CallRecord", on_delete=models.CASCADE, related_name="transcript"
    )
    turns = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)


class CallRecording(models.Model):
    """The audio of a call, recorded by the voice worker after the person
    agreed, and kept encrypted in private storage (recordings.py).

    The file is never served by URL from storage: Django streams it to people
    who may see the call, through a short-lived link, and logs every access.
    """

    class Status(models.TextChoices):
        RECORDING = "recording", "Recording"
        PROCESSING = "processing", "Processing"
        AVAILABLE = "available", "Available"
        FAILED = "failed", "Failed"
        NOT_RECORDED = "not_recorded", "Not recorded"
        DELETED = "deleted", "Deleted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    call = models.OneToOneField(
        "telephony.CallRecord", on_delete=models.CASCADE, related_name="recording"
    )
    # Kept on the row too, so isolation never depends on a join.
    organisation = models.ForeignKey(
        "tenancy.Organisation", on_delete=models.CASCADE, related_name="recordings"
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.RECORDING, db_index=True
    )
    # Why there is no audio, or what went wrong, in plain words.
    reason = models.CharField(max_length=300, blank=True)
    storage = models.CharField(max_length=8, blank=True)
    # "<organisation id>/<call id>.ogg.enc": no number or name in it.
    object_key = models.CharField(max_length=200, blank=True)
    size_bytes = models.PositiveIntegerField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    # Seconds from the start of the conversation to the start of the
    # recording (the person's consent), to line the audio up with the
    # transcript's times.
    offset_seconds = models.FloatField(default=0)
    consented_at = models.DateTimeField(null=True, blank=True)
    stored_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def playable(self) -> bool:
        return self.status == self.Status.AVAILABLE and bool(self.object_key)


class CallResult(models.Model):
    """The AI's reading of a call: summary, outcome and the answers it heard."""

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting to be analysed"
        DONE = "done", "Analysed"
        UNAVAILABLE = "unavailable", "Cannot be analysed"
        FAILED = "failed", "Analysis failed"

    class Outcome(models.TextChoices):
        INTERESTED = "interested", "Interested"
        CALLBACK = "callback", "Asked for a call back"
        INFORMATION = "information", "Got the information they needed"
        NOT_INTERESTED = "not_interested", "Not interested"
        WRONG_NUMBER = "wrong_number", "Wrong person or number"
        OPTED_OUT = "opted_out", "Asked not to be called"
        NO_CONVERSATION = "no_conversation", "No real conversation"
        OTHER = "other", "Other"

    class Resolution(models.TextChoices):
        RESOLVED = "resolved", "Resolved"
        PARTLY = "partly", "Partly resolved"
        UNRESOLVED = "unresolved", "Not resolved"
        NOT_APPLICABLE = "na", "No request to resolve"

    class Adherence(models.TextChoices):
        FOLLOWED = "followed", "Followed its instructions"
        PARTLY = "partly", "Partly followed its instructions"
        NOT_FOLLOWED = "not_followed", "Did not follow its instructions"
        UNKNOWN = "unknown", "Not enough conversation to judge"

    call = models.OneToOneField(
        "telephony.CallRecord", on_delete=models.CASCADE, related_name="result"
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING
    )
    outcome = models.CharField(max_length=16, choices=Outcome.choices, blank=True)
    summary = models.TextField(max_length=1500, blank=True)
    answers = models.JSONField(default=dict, blank=True)
    # What the call was about, read against the employee's own job
    # (analysis.py). Lists hold short plain strings unless noted.
    reason = models.CharField(max_length=300, blank=True)
    topics = models.JSONField(default=list, blank=True)
    info_given = models.JSONField(default=list, blank=True)
    # Other things the person said that matter: [{"label", "value"}].
    details = models.JSONField(default=list, blank=True)
    resolution = models.CharField(max_length=10, choices=Resolution.choices, blank=True)
    # One of the employee's own "possible results", or "" if none fits.
    result_label = models.CharField(max_length=60, blank=True, db_index=True)
    pending = models.JSONField(default=list, blank=True)
    next_step = models.CharField(max_length=300, blank=True)
    follow_up_needed = models.BooleanField(default=False, db_index=True)
    follow_up_reason = models.CharField(max_length=300, blank=True)
    adherence = models.CharField(max_length=12, choices=Adherence.choices, blank=True)
    # [{"note", "quote"}]: each quote was found in the transcript.
    adherence_notes = models.JSONField(default=list, blank=True)
    # [{"kind", "detail", "quote", "source"}]: "data" issues were measured,
    # "ai" issues were read from the transcript and their quote was found in it.
    issues = models.JSONField(default=list, blank=True)
    has_issues = models.BooleanField(default=False, db_index=True)
    # When the conversation arrived from the worker, and when it was deleted
    # under the organisation's setting. A repeated report is never stored again.
    transcript_received_at = models.DateTimeField(null=True, blank=True)
    transcript_deleted_at = models.DateTimeField(null=True, blank=True)
    callback_requested = models.BooleanField(default=False)
    callback_when = models.CharField(max_length=80, blank=True)
    opt_out_requested = models.BooleanField(default=False)
    # A specific day and time the person agreed to, for a booking action.
    appointment_at = models.DateTimeField(null=True, blank=True)
    whatsapp_requested = models.BooleanField(default=False)
    human_requested = models.BooleanField(default=False)
    model = models.CharField(max_length=80, blank=True)
    error = models.CharField(max_length=200, blank=True)
    # Failed analyses are retried with growing gaps, then given up (and, in the
    # "analyse then delete" mode, the transcript is deleted all the same).
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    analysed_at = models.DateTimeField(null=True, blank=True)
    actions_applied_at = models.DateTimeField(null=True, blank=True)


class CallAction(models.Model):
    """Something the system did, or decided not to do, because of a call.
    Only actions the backend actually carried out are marked as done."""

    class Status(models.TextChoices):
        DONE = "done", "Done"
        SKIPPED = "skipped", "Not done"
        FAILED = "failed", "Failed"

    call = models.ForeignKey(
        "telephony.CallRecord", on_delete=models.CASCADE, related_name="actions"
    )
    action = models.CharField(max_length=30)
    status = models.CharField(max_length=8, choices=Status.choices)
    detail = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")


class MissedCall(models.Model):
    """An inbound caller someone must call back: the number was not routed to
    a ready employee, or the call came after hours."""

    class Reason(models.TextChoices):
        NOT_ROUTED = "not_routed", "No employee was ready to answer"
        AFTER_HOURS = "after_hours", "Called after hours"

    call = models.OneToOneField(
        "telephony.CallRecord", on_delete=models.CASCADE, related_name="missed"
    )
    reason = models.CharField(max_length=12, choices=Reason.choices)
    detail = models.CharField(max_length=200, blank=True)
    handled_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)


class CallAttempt(models.Model):
    """Every request to place an outbound call from the dashboard: placed,
    simulated (business calling switched off) or refused, with the reason.
    Numbers are kept masked; the call record holds the rest."""

    class Mode(models.TextChoices):
        TEST = "test", "Test call"
        BUSINESS = "business", "Business call"

    class Outcome(models.TextChoices):
        PLACED = "placed", "Placed"
        SIMULATED = "simulated", "Checked, not dialled"
        REFUSED = "refused", "Refused"

    organisation = models.ForeignKey(
        "tenancy.Organisation", on_delete=models.CASCADE, related_name="+"
    )
    campus = models.ForeignKey(
        "tenancy.Campus",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    user = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    mode = models.CharField(max_length=10, choices=Mode.choices)
    contact = models.ForeignKey(
        "crm.Contact",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    masked_number = models.CharField(max_length=24, blank=True)
    employee_version = models.ForeignKey(
        "workforce.EmployeeVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    caller_id = models.CharField(max_length=24, blank=True)
    purpose = models.CharField(max_length=160, blank=True)
    outcome = models.CharField(max_length=10, choices=Outcome.choices)
    reason = models.CharField(max_length=300, blank=True)
    call = models.ForeignKey(
        "telephony.CallRecord",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attempts",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)


class CallList(models.Model):
    """Numbers typed in or uploaded together, called one after another
    (calllists.py). Who speaks, what the calls are about, and the person's
    confirmation that everyone on it agreed to be called."""

    class Status(models.TextChoices):
        READY = "ready", "Ready to start"
        RUNNING = "running", "Calling"
        PAUSED = "paused", "Paused"
        DONE = "done", "Finished"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        "tenancy.Organisation", on_delete=models.CASCADE, related_name="call_lists"
    )
    campus = models.ForeignKey(
        "tenancy.Campus",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="call_lists",
    )
    # None only on lists made before every call needed an employee.
    employee = models.ForeignKey(
        "workforce.Employee",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="call_lists",
    )
    name = models.CharField(max_length=120)
    purpose = models.CharField(max_length=160, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.READY, db_index=True
    )
    # Why it is waiting right now (outside calling hours, another call on the
    # line), in plain words, or "".
    waiting = models.CharField(max_length=200, blank=True)
    # Set when the person starts the calls, confirming everyone agreed.
    consent_confirmed_at = models.DateTimeField(null=True, blank=True)
    consent_text = models.CharField(max_length=300, blank=True)
    # Rows of the typed or uploaded list that could not be used, and why.
    rejected_rows = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.name


class CallListEntry(models.Model):
    """One person on a call list. The number is stored encrypted and shown
    masked; it is decrypted only to dial."""

    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting"
        CALLING = "calling", "Calling now"
        ANSWERED = "answered", "Answered"
        NO_ANSWER = "no_answer", "No answer"
        FAILED = "failed", "Could not call"
        SKIPPED = "skipped", "Not called"

    call_list = models.ForeignKey(
        CallList, on_delete=models.CASCADE, related_name="entries"
    )
    position = models.PositiveIntegerField()
    name = models.CharField(max_length=120, blank=True)
    phone_encrypted = models.TextField(editable=False)
    phone_lookup = models.CharField(max_length=64, editable=False)
    phone_masked = models.CharField(max_length=24)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.WAITING, db_index=True
    )
    reason = models.CharField(max_length=200, blank=True)
    call = models.ForeignKey(
        "telephony.CallRecord",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="list_entries",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("position",)
