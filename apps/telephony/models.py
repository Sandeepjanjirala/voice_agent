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
