"""Connections to outside services, the actions employees may take with them,
and the record of every action that ran.

Credentials are stored encrypted (apps/integrations/secrets.py) and are never
shown again after they are saved. An action is a fixed, reviewed request: the
model never writes a URL, a path or code; it only fills in validated inputs.
"""

import uuid

from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL


class Integration(models.Model):
    class Kind(models.TextChoices):
        CAMPUS_CALENDAR = "campus_calendar", "Location calendar (built in)"
        GOOGLE_CALENDAR = "google_calendar", "Google Calendar"
        WHATSAPP = "whatsapp_cloud", "WhatsApp Business (Cloud API)"
        REST_API = "rest_api", "Approved REST API"
        WEBHOOK = "webhook", "Outgoing webhook"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        "tenancy.Organisation", on_delete=models.CASCADE, related_name="integrations"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    name = models.CharField(max_length=120)
    # Settings that are not secret: base URL, calendar id, phone number id...
    config = models.JSONField(default=dict, blank=True)
    # Secrets, encrypted as one JSON object. Never sent to a page.
    secret_encrypted = models.TextField(blank=True, editable=False)
    secret_hint = models.CharField(max_length=40, blank=True, editable=False)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_check = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class ApprovedAction(models.Model):
    """One thing an employee or a staff member may do, reviewed by an admin.
    Employees use it only when it is switched on for them."""

    class Kind(models.TextChoices):
        CHECK_AVAILABILITY = "check_availability", "Check calendar availability"
        BOOK_APPOINTMENT = "book_appointment", "Book an appointment"
        WHATSAPP_TEMPLATE = "whatsapp_template", "Send an approved WhatsApp template"
        REST_REQUEST = "rest_request", "Call an approved REST endpoint"
        UPDATE_LEAD = "update_lead", "Update the lead"
        CREATE_FOLLOW_UP = "create_follow_up", "Create a follow-up task"
        ESCALATE = "escalate", "Hand over to a person"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        "tenancy.Organisation", on_delete=models.CASCADE, related_name="actions"
    )
    key = models.SlugField(max_length=40)
    label = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    integration = models.ForeignKey(
        Integration,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="actions",
    )
    # Kind-specific settings, e.g. method and path for a REST request.
    settings = models.JSONField(default=dict, blank=True)
    # What the inputs must look like; checked before anything runs.
    input_schema = models.JSONField(default=dict, blank=True)
    is_enabled = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("label",)
        constraints = (
            models.UniqueConstraint(
                fields=["organisation", "key"], name="one_action_key_per_organisation"
            ),
        )

    def __str__(self) -> str:
        return self.label

    @property
    def employee_key(self) -> str:
        """How an employee's configuration refers to this action."""
        return f"custom:{self.key}"


class ActionRun(models.Model):
    """One attempt to run an action, kept whatever happened."""

    class Status(models.TextChoices):
        SUCCEEDED = "succeeded", "Done"
        SIMULATED = "simulated", "Simulated (not sent)"
        REFUSED = "refused", "Refused"
        FAILED = "failed", "Failed"

    class Trigger(models.TextChoices):
        STAFF = "staff", "A team member"
        CALL = "call", "After a call"
        TEST = "test", "Test run"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        "tenancy.Organisation", on_delete=models.CASCADE, related_name="+"
    )
    action = models.ForeignKey(
        ApprovedAction,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="runs",
    )
    # Kept as text so the record still reads after the action is changed.
    action_key = models.CharField(max_length=60)
    action_label = models.CharField(max_length=120)
    kind = models.CharField(max_length=20)
    trigger = models.CharField(max_length=8, choices=Trigger.choices)
    actor = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    call = models.ForeignKey(
        "telephony.CallRecord",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="action_runs",
    )
    lead = models.ForeignKey(
        "crm.Lead",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="action_runs",
    )
    status = models.CharField(max_length=10, choices=Status.choices)
    # Inputs and output with numbers masked and secrets never present.
    inputs = models.JSONField(default=dict, blank=True)
    output = models.JSONField(default=dict, blank=True)
    message = models.CharField(max_length=300, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)


class Appointment(models.Model):
    """A booking in the built-in campus calendar, or a copy of one made in an
    outside calendar."""

    class Status(models.TextChoices):
        BOOKED = "booked", "Booked"
        # Sent to an outside calendar, but its answer did not arrive (timeout).
        # The slot stays held; running the booking again is safe (same event ID).
        UNCONFIRMED = "unconfirmed", "Not confirmed yet"
        CANCELLED = "cancelled", "Cancelled"

    HOLDING = (Status.BOOKED, Status.UNCONFIRMED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        "tenancy.Organisation", on_delete=models.CASCADE, related_name="+"
    )
    campus = models.ForeignKey(
        "tenancy.Campus", on_delete=models.CASCADE, related_name="appointments"
    )
    lead = models.ForeignKey(
        "crm.Lead",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="appointments",
    )
    title = models.CharField(max_length=160)
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField()
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.BOOKED
    )
    external_id = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("starts_at",)
        constraints = (
            # One booking per campus slot, whatever races in: the database
            # refuses the second one even if two requests check at once.
            models.UniqueConstraint(
                fields=["campus", "starts_at"],
                condition=models.Q(status__in=("booked", "unconfirmed")),
                name="one_booking_per_campus_slot",
            ),
        )

    @property
    def google_event_id(self) -> str:
        """The event ID used in Google Calendar: the same on every retry, so a
        repeated request cannot make a second event (base32hex: 0-9, a-v)."""
        return f"sca{self.pk.hex}"


class WebhookDelivery(models.Model):
    """One event to deliver to one webhook, retried until it is accepted."""

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Gave up"
        SIMULATED = "simulated", "Simulated (not sent)"
        CANCELLED = "cancelled", "Cancelled: webhook switched off"

    integration = models.ForeignKey(
        Integration, on_delete=models.CASCADE, related_name="deliveries"
    )
    event = models.CharField(max_length=40)
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
