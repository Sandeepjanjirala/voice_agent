"""People an organisation may call, what it knows about its right to call
them, and the enquiries (leads) and follow-up work that hang off them.

A phone number is stored three ways and never in the clear: encrypted (to dial
it), as a keyed hash (to match it) and masked (to show it). See crypto.py.
"""

import uuid

from django.conf import settings
from django.db import models

from apps.tenancy.models import Campus, Organisation

USER = settings.AUTH_USER_MODEL


class ContactImport(models.Model):
    """One import of a list, kept as evidence of who brought the contacts in
    and what they confirmed about the right to call them."""

    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE)
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT)
    created_by = models.ForeignKey(
        USER, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    origin = models.CharField(max_length=12)
    calling_purpose = models.CharField(max_length=200)
    consent_attested = models.BooleanField(default=False)
    consent_evidence = models.TextField(max_length=1000, blank=True)
    rows = models.PositiveIntegerField(default=0)
    created = models.PositiveIntegerField(default=0)
    duplicates = models.PositiveIntegerField(default=0)
    invalid = models.PositiveIntegerField(default=0)
    opted_out = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("-created_at",)


class Contact(models.Model):
    class Origin(models.TextChoices):
        ENQUIRY = "enquiry", "Enquiry form"
        PHONE = "phone", "Phone enquiry"
        WALK_IN = "walk_in", "Walk-in"
        REFERRAL = "referral", "Referral"
        EXISTING = "existing", "Existing relationship"
        IMPORT = "import", "Imported list"
        OTHER = "other", "Other"

    class Consent(models.TextChoices):
        UNKNOWN = "unknown", "Not recorded"
        GRANTED = "granted", "Agreed to be called"
        DENIED = "denied", "Did not agree"
        WITHDRAWN = "withdrawn", "Withdrew"

    class Language(models.TextChoices):
        ANY = "", "No preference"
        TELUGU = "te", "Telugu"
        ENGLISH = "en", "English"
        HINDI = "hi", "Hindi"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="contacts"
    )
    campus = models.ForeignKey(
        Campus, on_delete=models.PROTECT, related_name="contacts"
    )
    name = models.CharField(max_length=120)
    phone_encrypted = models.TextField(blank=True)
    phone_lookup = models.CharField(max_length=64)
    phone_masked = models.CharField(max_length=24, blank=True)

    # Why the organisation has this person's number, and what it may use it for.
    origin = models.CharField(
        max_length=12, choices=Origin.choices, default=Origin.ENQUIRY
    )
    origin_detail = models.CharField(max_length=200, blank=True)
    calling_purpose = models.CharField(max_length=200, blank=True)
    consent = models.CharField(
        max_length=10, choices=Consent.choices, default=Consent.UNKNOWN
    )
    consent_evidence = models.TextField(max_length=1000, blank=True)
    consent_at = models.DateTimeField(null=True, blank=True)
    consent_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    preferred_language = models.CharField(
        max_length=2, choices=Language.choices, blank=True
    )
    best_time = models.CharField(max_length=80, blank=True)

    source_import = models.ForeignKey(
        ContactImport, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_contacted_at = models.DateTimeField(null=True, blank=True)
    # Set when the personal details were removed; the row stays for history.
    erased_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = (
            models.UniqueConstraint(
                fields=["organisation", "phone_lookup"],
                name="one_contact_per_number_per_organisation",
            ),
        )

    def __str__(self) -> str:
        return f"{self.name} ({self.phone_masked or 'erased'})"

    @property
    def is_erased(self) -> bool:
        return self.erased_at is not None


class OptOut(models.Model):
    """A number the organisation must not call, whichever campus asks.
    It outlives the contact: erasing a person must not make them callable."""

    class Source(models.TextChoices):
        CALLER = "caller", "Asked during a call"
        STAFF = "staff", "Added by staff"
        IMPORT = "import", "Imported"

    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="opt_outs"
    )
    phone_encrypted = models.TextField()
    phone_lookup = models.CharField(max_length=64)
    phone_masked = models.CharField(max_length=24)
    reason = models.CharField(max_length=200, blank=True)
    source = models.CharField(
        max_length=8, choices=Source.choices, default=Source.STAFF
    )
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = (
            models.UniqueConstraint(
                fields=["organisation", "phone_lookup"],
                name="one_opt_out_per_number_per_organisation",
            ),
        )

    def __str__(self) -> str:
        return f"Do not call {self.phone_masked}"


class Lead(models.Model):
    """One enquiry or piece of business with a contact, moving through stages.
    The stages are general on purpose: they suit any kind of enquiry."""

    class Stage(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        INTERESTED = "interested", "Interested"
        FOLLOW_UP = "follow_up", "Follow up"
        WON = "won", "Converted"
        LOST = "lost", "Closed"

    OPEN_STAGES = (Stage.NEW, Stage.CONTACTED, Stage.INTERESTED, Stage.FOLLOW_UP)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="leads"
    )
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, related_name="leads")
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="leads")
    subject = models.CharField(max_length=160)
    details = models.TextField(max_length=2000, blank=True)
    stage = models.CharField(
        max_length=10, choices=Stage.choices, default=Stage.NEW, db_index=True
    )
    assigned_to = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="leads"
    )
    # The AI employee handling this lead, if any.
    employee = models.ForeignKey(
        "workforce.Employee",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leads",
    )
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.subject

    @property
    def is_open(self) -> bool:
        return self.stage in self.OPEN_STAGES


class LeadNote(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    text = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)


class FollowUp(models.Model):
    """Something a person has to do about a lead by a given time."""

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="follow_ups")
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE)
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT)
    title = models.CharField(max_length=160)
    due_at = models.DateTimeField(db_index=True)
    assigned_to = models.ForeignKey(
        USER,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="follow_ups",
    )
    done_at = models.DateTimeField(null=True, blank=True)
    done_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("due_at",)

    def __str__(self) -> str:
        return self.title
