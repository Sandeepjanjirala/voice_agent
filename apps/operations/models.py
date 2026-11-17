"""What the platform did in the background, and what people need to know.

JobRun records every scheduled job run, so failures are visible on the
Operations page. Notification is the bell: things a person must act on.
UsageDay is the daily usage snapshot written by the reporting job.
"""

from django.conf import settings
from django.db import models


class JobRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        OK = "ok", "Done"
        SKIPPED = "skipped", "Skipped (already running)"
        FAILED = "failed", "Failed"

    name = models.CharField(max_length=60, db_index=True)
    status = models.CharField(
        max_length=8, choices=Status.choices, default=Status.RUNNING
    )
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ("-started_at", "-id")

    def __str__(self) -> str:
        return f"{self.name} {self.status}"

    @property
    def seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at).total_seconds(), 1)


class Notification(models.Model):
    """One thing to look at. With no user, every admin of the organisation
    (and the campus's staff, when a campus is set) sees it."""

    class Kind(models.TextChoices):
        FOLLOW_UP = "follow_up", "Follow-up due"
        MISSED_CALL = "missed_call", "Missed caller"
        ESCALATION = "escalation", "Caller asked for a person"
        WEBHOOK = "webhook", "Webhook failing"
        ACTION = "action", "Action failed"
        OPT_OUT = "opt_out", "Caller opted out"
        JOB = "job", "Background job failed"

    organisation = models.ForeignKey(
        "tenancy.Organisation", on_delete=models.CASCADE, related_name="+"
    )
    campus = models.ForeignKey(
        "tenancy.Campus",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    kind = models.CharField(max_length=12, choices=Kind.choices)
    title = models.CharField(max_length=200)
    url = models.CharField(max_length=300, blank=True)
    # Stops the same thing being announced twice (e.g. "follow-up:<id>").
    key = models.CharField(max_length=80, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = (
            models.UniqueConstraint(
                fields=["organisation", "key"],
                condition=~models.Q(key=""),
                name="one_notification_per_key",
            ),
        )


class UsageDay(models.Model):
    """Usage of one organisation on one day, by campus and employee."""

    organisation = models.ForeignKey(
        "tenancy.Organisation", on_delete=models.CASCADE, related_name="+"
    )
    day = models.DateField(db_index=True)
    campus = models.ForeignKey(
        "tenancy.Campus",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    employee = models.ForeignKey(
        "workforce.Employee",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    calls = models.PositiveIntegerField(default=0)
    answered = models.PositiveIntegerField(default=0)
    talk_seconds = models.PositiveIntegerField(default=0)
    analyses = models.PositiveIntegerField(default=0)
    actions = models.PositiveIntegerField(default=0)
    messages = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # One row per organisation, day, campus and employee: the reporting job
        # is the only writer and upserts, so no database constraint is needed
        # (a unique constraint would treat empty campus or employee as distinct).
        ordering = ("-day",)
