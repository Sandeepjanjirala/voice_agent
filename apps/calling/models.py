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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("number",)

    def __str__(self) -> str:
        return self.label or self.number


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

    call = models.OneToOneField(
        "telephony.CallRecord", on_delete=models.CASCADE, related_name="result"
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING
    )
    outcome = models.CharField(max_length=16, choices=Outcome.choices, blank=True)
    summary = models.TextField(max_length=1000, blank=True)
    answers = models.JSONField(default=dict, blank=True)
    callback_requested = models.BooleanField(default=False)
    callback_when = models.CharField(max_length=80, blank=True)
    opt_out_requested = models.BooleanField(default=False)
    model = models.CharField(max_length=80, blank=True)
    error = models.CharField(max_length=200, blank=True)
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
