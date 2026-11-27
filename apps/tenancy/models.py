"""Who the data belongs to: organisations, their campuses, and who may see what."""

import uuid

from django.conf import settings
from django.db import models


class Organisation(models.Model):
    """A customer of the platform. Nothing is shared between organisations."""

    class Transcripts(models.TextChoices):
        OFF = "off", "Do not keep what was said"
        ANALYSE = "analyse", "Use it for the call summary, then delete it"
        KEEP = "keep", "Keep the transcript with the call"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, unique=True)
    # What a caller says is personal data: by default it is used for the call
    # summary and then deleted (owner's decision, 17 Nov 2026).
    transcripts = models.CharField(
        max_length=8, choices=Transcripts.choices, default=Transcripts.ANALYSE
    )
    # Whether LiveKit Cloud may keep each call's audio, transcript, traces and
    # logs (Agent observability). Off: every call opts out, whatever the
    # LiveKit project setting is. Deleting data here never deletes theirs.
    cloud_observability = models.BooleanField(default=False)

    class Recording(models.TextChoices):
        OFF = "off", "Never record calls"
        DEMO = "demo", "Demo test calls, without a recording question"
        TEST_CALLS = "test", "Record approved test calls only, after consent"
        WITH_CONSENT = "consent", "Record any call, after the person agrees on the call"

    # Our own call recordings (apps/calling/recordings.py), separate from
    # LiveKit Cloud's copy above. Off unless an admin switches it on; even
    # then a call requires verbal consent, except explicitly approved demo tests.
    recording = models.CharField(
        max_length=8, choices=Recording.choices, default=Recording.OFF
    )
    # Said straight after the opening line on calls that may be recorded.
    recording_notice = models.CharField(
        max_length=300,
        default="This call may be recorded so our team can review it. Is that okay with you?",
    )
    # Your own rates, for the usage page's cost estimate (rupees). The
    # providers' bills are what counts; these only make an estimate.
    cost_per_minute = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    cost_per_message = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    cost_per_analysis = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    # Business calling limits. The budget is talk minutes per calendar month
    # (0: no budget). Concurrency is also capped by the phone line: the current
    # outbound line carries one call at a time.
    monthly_call_minutes = models.PositiveIntegerField(default=0)
    max_concurrent_calls = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class Campus(models.Model):
    """A branch or site of an organisation. Records belong to one campus."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="campuses"
    )
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)
    # Length of one appointment in the built-in campus calendar.
    slot_minutes = models.PositiveSmallIntegerField(default=30)
    # Said by an employee answering after hours, before taking a message.
    after_hours_message = models.CharField(max_length=200, blank=True)
    # Who is told when a caller asks for a person.
    escalation_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "location"
        verbose_name_plural = "locations"
        constraints = (
            models.UniqueConstraint(
                fields=["organisation", "name"], name="one_campus_name_per_organisation"
            ),
        )

    def __str__(self) -> str:
        return self.name


class Membership(models.Model):
    """A person's place in one organisation. Platform superusers have none."""

    class Role(models.TextChoices):
        ORG_ADMIN = "org_admin", "Organisation admin"
        CAMPUS_STAFF = "campus_staff", "Location team member"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="membership"
    )
    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    # Used for campus staff only: the campuses they may work in.
    campuses = models.ManyToManyField(Campus, blank=True, related_name="memberships")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("user__username",)

    def __str__(self) -> str:
        return f"{self.user} ({self.get_role_display()})"


class RetentionPolicy(models.Model):
    """How long an organisation keeps personal data. Zero means "keep"."""

    organisation = models.OneToOneField(
        Organisation, on_delete=models.CASCADE, related_name="retention"
    )
    contact_days = models.PositiveIntegerField(
        default=0,
        help_text="Erase a contact this many days after its last activity, "
        "unless it has an open lead. 0 keeps contacts.",
    )
    call_days = models.PositiveIntegerField(
        default=0, help_text="Delete call records older than this. 0 keeps them."
    )
    transcript_days = models.PositiveIntegerField(
        default=30,
        help_text="Delete kept call transcripts older than this. 0 keeps them.",
    )
    recording_days = models.PositiveIntegerField(
        default=30,
        help_text="Delete call recordings older than this. 0 keeps them.",
    )
    audit_days = models.PositiveIntegerField(
        default=0, help_text="Delete access-log entries older than this. 0 keeps them."
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Retention for {self.organisation}"


class AuditEvent(models.Model):
    """Who looked at or changed personal data. Never holds a phone number."""

    organisation = models.ForeignKey(
        Organisation,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="audit_events",
    )
    campus = models.ForeignKey(
        Campus, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # Kept as text so the entry still reads after the account is removed.
    actor_name = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=40, db_index=True)
    target_type = models.CharField(max_length=40, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_name} {self.action}"


class OpeningHours(models.Model):
    """When a campus is open on one weekday (Monday = 0). A campus with no
    rows is treated as always open."""

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="hours")
    weekday = models.PositiveSmallIntegerField()
    opens = models.TimeField()
    closes = models.TimeField()

    class Meta:
        ordering = ("weekday", "opens")
        constraints = (
            models.UniqueConstraint(
                fields=["campus", "weekday"], name="one_opening_per_campus_weekday"
            ),
        )


class Holiday(models.Model):
    """A closed day, for one campus or the whole organisation."""

    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="holidays"
    )
    campus = models.ForeignKey(
        Campus, null=True, blank=True, on_delete=models.CASCADE, related_name="holidays"
    )
    date = models.DateField(db_index=True)
    name = models.CharField(max_length=80)

    class Meta:
        ordering = ("date",)
