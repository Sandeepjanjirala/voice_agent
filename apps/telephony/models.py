"""Call records with encrypted phone numbers and redacted log representations."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class CallRecord(models.Model):
    class Status(models.TextChoices):
        INITIATING = "initiating", "Initiating"
        RINGING = "ringing", "Ringing"
        ANSWERED = "answered", "Answered"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"
        NO_ANSWER = "no_answer", "No answer"
        FAILED = "failed", "Failed"

    class Direction(models.TextChoices):
        OUTBOUND = "outbound", "Outbound"
        INBOUND = "inbound", "Inbound"

    class Source(models.TextChoices):
        """Who set the current status; decides what may overwrite it."""

        DJANGO = "django", "Django"
        LIVEKIT = "livekit", "LiveKit room state"
        WORKER = "worker", "Voice worker"
        OPERATOR = "operator", "Operator"
        INFERRED = "inferred", "Inferred"
        TIMEOUT = "timeout", "Timed out"

    class EndReason(models.TextChoices):
        """Why a conversation ended, as the voice worker saw it."""

        CALLER = "caller_hung_up", "The person hung up"
        AGENT = "agent_ended", "The AI employee ended the call"
        TIME_LIMIT = "time_limit", "The call reached its time limit"
        ERROR = "error", "A technical error ended the call"
        SYSTEM = "system", "Ended by the system"

    ACTIVE = (Status.INITIATING, Status.RINGING, Status.ANSWERED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    direction = models.CharField(
        max_length=8, choices=Direction.choices, default=Direction.OUTBOUND
    )
    room = models.CharField(max_length=64, unique=True)
    masked_number = models.CharField(max_length=24)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.INITIATING, db_index=True
    )
    status_source = models.CharField(
        max_length=8, choices=Source.choices, default=Source.DJANGO
    )
    failure_reason = models.CharField(max_length=200, blank=True)
    end_reason = models.CharField(max_length=16, choices=EndReason.choices, blank=True)
    sip_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    sip_status = models.CharField(max_length=64, blank=True)
    placed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="placed_calls",
    )

    # Whose call this is. Empty on records made before organisations existed
    # and on calls placed from the command line.
    organisation = models.ForeignKey(
        "tenancy.Organisation",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="call_records",
    )
    campus = models.ForeignKey(
        "tenancy.Campus",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="call_records",
    )
    lead = models.ForeignKey(
        "crm.Lead",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="call_records",
    )
    # The published employee version the call used, once calls use employees.
    employee_version = models.ForeignKey(
        "workforce.EmployeeVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="call_records",
    )
    # The person on the line, when the system knows who it is. Empty for a
    # rehearsal, where the approved test number stands in for the contact.
    contact = models.ForeignKey(
        "crm.Contact",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="call_records",
    )
    # Exactly what the voice worker was given for this call: employee, version,
    # script, voice and knowledge. Written once when the call starts.
    context_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    # The actual remote number for inbound and outbound calls, encrypted and as
    # a match value (apps/crm/crypto). Rehearsals keep their test destination.
    remote_encrypted = models.TextField(blank=True, editable=False)
    remote_lookup = models.CharField(max_length=64, blank=True, editable=False)
    notes = models.TextField(max_length=2000, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    ringing_at = models.DateTimeField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Talk time: from answer to hang-up.
    duration_seconds = models.FloatField(null=True, blank=True)

    agent_turns = models.PositiveSmallIntegerField(null=True, blank=True)
    user_turns = models.PositiveSmallIntegerField(null=True, blank=True)
    interruptions = models.PositiveSmallIntegerField(null=True, blank=True)
    e2e_latency_p50 = models.FloatField(null=True, blank=True)
    e2e_latency_max = models.FloatField(null=True, blank=True)
    llm_ttft_p50 = models.FloatField(null=True, blank=True)
    tts_ttfb_p50 = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = (
            # One outbound call at a time, enforced by the database itself.
            models.UniqueConstraint(
                fields=["direction"],
                condition=Q(direction="outbound")
                & Q(status__in=["initiating", "ringing", "answered"]),
                name="one_active_outbound_call",
            ),
        )

    def __str__(self) -> str:
        return (
            f"{self.get_direction_display()} call {self.masked_number} ({self.status})"
        )

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE

    @property
    def destination_number(self) -> str:
        from .numbers import destination_number

        return destination_number(self)

    @property
    def display_number(self) -> str:
        from apps.crm.phone_display import UNAVAILABLE

        return self.destination_number or UNAVAILABLE

    @property
    def is_rehearsal(self) -> bool:
        """Placed to the approved test number on behalf of a lead's contact."""
        return bool(self.context_snapshot.get("rehearsal"))

    @classmethod
    def from_db(cls, db, field_names, values):
        record = super().from_db(db, field_names, values)
        record._loaded_snapshot = record.__dict__.get("context_snapshot")
        return record

    def save(self, *args, **kwargs):
        loaded = getattr(self, "_loaded_snapshot", None)
        if loaded and self.context_snapshot != loaded:
            raise ValueError("A call's context snapshot cannot be changed.")
        super().save(*args, **kwargs)
        self._loaded_snapshot = self.context_snapshot


class AgentProfile(models.Model):
    """What the agent is for. One profile is active; it is sent to the voice
    worker with every call and browser conversation."""

    # Sent to the LLM on every turn, so bounded together by
    # EMPLOYEE_INSTRUCTION_BYTES (the same budget as AI employees).
    PROMPT_FIELDS = (
        "agent_name",
        "call_purpose",
        "objectives",
        "limitations",
        "ending",
    )

    name = models.CharField(max_length=60, unique=True)
    is_active = models.BooleanField(default=False)
    business_name = models.CharField(max_length=80, blank=True)
    agent_name = models.CharField(max_length=60, blank=True)
    call_purpose = models.TextField(max_length=1200, blank=True)
    greeting = models.TextField(max_length=400, blank=True)
    objectives = models.TextField(max_length=4000, blank=True)
    limitations = models.TextField(max_length=1500, blank=True)
    ending = models.TextField(max_length=600, blank=True)
    business_info = models.TextField(max_length=8000, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["is_active"],
                condition=Q(is_active=True),
                name="one_active_agent_profile",
            ),
        )

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        from django.conf import settings
        from django.core.exceptions import ValidationError

        size = self.prompt_size()
        budget = settings.EMPLOYEE_INSTRUCTION_BYTES
        if size > budget:
            raise ValidationError(
                "Purpose, goals, limits and ending are too long together "
                f"({size:,} of {budget:,} bytes; a Telugu letter takes three). "
                "Shorten the longest one, or move facts into Business information, "
                "which the agent looks up only when asked."
            )

    def prompt_size(self) -> int:
        return sum(len(getattr(self, f).encode("utf-8")) for f in self.PROMPT_FIELDS)

    def payload(self) -> dict[str, str]:
        """What the voice worker receives (voice_agent/profile.py)."""
        return {
            "business_name": self.business_name,
            "agent_name": self.agent_name,
            "purpose": self.call_purpose,
            "greeting": self.greeting,
            "objectives": self.objectives,
            "limitations": self.limitations,
            "ending": self.ending,
            "business_info": self.business_info,
        }
