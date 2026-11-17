"""Call records. One row per call; phone numbers are stored masked only."""

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
    # An inbound caller's number, encrypted and as a match value (apps/crm/crypto),
    # so staff can turn the call into a lead without the number ever being shown.
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

    # The worker gives the prompt section 1024 bytes; leave room for its labels.
    PROMPT_FIELDS = (
        "agent_name",
        "call_purpose",
        "objectives",
        "limitations",
        "ending",
    )
    PROMPT_BYTES = 800

    name = models.CharField(max_length=60, unique=True)
    is_active = models.BooleanField(default=False)
    business_name = models.CharField(max_length=80, blank=True)
    agent_name = models.CharField(max_length=40, blank=True)
    call_purpose = models.CharField(max_length=300, blank=True)
    greeting = models.CharField(max_length=250, blank=True)
    objectives = models.TextField(max_length=400, blank=True)
    limitations = models.TextField(max_length=300, blank=True)
    ending = models.CharField(max_length=200, blank=True)
    business_info = models.TextField(max_length=4000, blank=True)
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
        from django.core.exceptions import ValidationError

        size = sum(len(getattr(self, f).encode("utf-8")) for f in self.PROMPT_FIELDS)
        if size > self.PROMPT_BYTES:
            raise ValidationError(
                "Purpose, goals, limits and ending are too long together "
                f"({size} of {self.PROMPT_BYTES} bytes; Telugu letters count as three). "
                "Shorten them, or move facts into Business information."
            )

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
