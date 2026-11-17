"""AI employees: a named calling role built from a plain-language brief."""

import uuid

from django.conf import settings
from django.db import models


class Employee(models.Model):
    class Channel(models.TextChoices):
        """The one job an employee is built for."""

        INSTANT = "instant", "Calls new enquiries"
        CAMPAIGN = "campaign", "Works through a call list"
        INBOUND = "inbound", "Answers incoming calls"

    class Language(models.TextChoices):
        TELUGU_ENGLISH = "te-en", "Telugu + English"
        TELUGU = "te", "Telugu"
        ENGLISH = "en", "English"

    class DraftSource(models.TextChoices):
        AI = "ai", "Drafted by AI"
        TEMPLATE = "template", "Started from a template"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        "tenancy.Organisation",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="employees",
    )
    # None chosen means the employee works for every campus.
    campuses = models.ManyToManyField(
        "tenancy.Campus", blank=True, related_name="employees"
    )
    name = models.CharField(max_length=40)
    role = models.CharField(max_length=80)
    channel = models.CharField(
        max_length=10, choices=Channel.choices, default=Channel.INSTANT
    )
    language = models.CharField(
        max_length=6, choices=Language.choices, default=Language.TELUGU_ENGLISH
    )
    # The owner's own description of the job, kept so the draft can be redone.
    brief = models.TextField(max_length=2000)
    # The working copy of the configuration (see config.py). Calls never read
    # this: they use a published version.
    draft = models.JSONField(default=dict)
    draft_source = models.CharField(
        max_length=8, choices=DraftSource.choices, default=DraftSource.AI
    )
    # The version calls use. Empty until the first publish.
    live_version = models.ForeignKey(
        "EmployeeVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # The owner's own switch: off pauses the employee without unpublishing it.
    taking_calls = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="employees_created",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.name} ({self.role})"

    @property
    def status(self) -> str:
        if self.live_version_id is None:
            return "draft"
        return "published" if self.taking_calls else "paused"

    @property
    def status_label(self) -> str:
        return {"draft": "Draft", "published": "Published", "paused": "Paused"}[
            self.status
        ]

    @property
    def initial(self) -> str:
        return self.name[:1].upper() or "?"


class EmployeeVersion(models.Model):
    """A published configuration. Never edited: publishing again adds a version,
    so a call can always be traced to exactly what the employee was told."""

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="versions"
    )
    number = models.PositiveIntegerField()
    config = models.JSONField()
    # What the voice worker receives for this version (see publishing.py).
    payload = models.JSONField()
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-number",)
        constraints = (
            models.UniqueConstraint(
                fields=["employee", "number"], name="one_number_per_employee_version"
            ),
        )

    def __str__(self) -> str:
        return f"{self.employee.name} v{self.number}"
