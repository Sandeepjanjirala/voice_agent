from datetime import time

from django import forms

from apps.crm.models import ContactImport
from apps.crm.phones import InvalidPhoneError, normalize_line
from apps.workforce.models import Employee, KnowledgeItem

from .models import Campaign, LeadSource, PhoneNumber
from .queue import EARLIEST, LATEST

WEEKDAYS = (
    (0, "Mon"),
    (1, "Tue"),
    (2, "Wed"),
    (3, "Thu"),
    (4, "Fri"),
    (5, "Sat"),
    (6, "Sun"),
)


def _employees(scope):
    return scope.limit_shared(Employee.objects.all())


def _window(form, start: str, end: str) -> None:
    data = form.cleaned_data
    begin, finish = data.get(start), data.get(end)
    if begin is None or finish is None:
        return
    if begin < EARLIEST or finish > LATEST:
        form.add_error(
            start,
            f"Calls may be placed only between {EARLIEST:%H:%M} and {LATEST:%H:%M}.",
        )
    if finish <= begin:
        form.add_error(end, "The window must end after it starts.")


class PhoneNumberForm(forms.ModelForm):
    class Meta:
        model = PhoneNumber
        fields = (
            "number",
            "label",
            "campus",
            "employee",
            "routing_enabled",
            "transfer_to",
        )
        labels = {  # noqa: RUF012
            "employee": "AI employee who answers",
            "routing_enabled": "Answer calls to this number with the employee",
            "transfer_to": "Transfer number for callers who ask for a person",
        }
        help_texts = {  # noqa: RUF012
            "number": "The business line, e.g. +91 40 1234 5678.",
            "campus": "Calls to this number belong to this campus and use its knowledge.",
            "transfer_to": "Kept for the transfer design. No call is transferred yet.",
        }

    def __init__(self, *args, scope, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.scope = scope
        self.fields["campus"].queryset = scope.campuses()
        self.fields["campus"].empty_label = "Whole organisation"
        self.fields["employee"].queryset = _employees(scope)
        self.fields["employee"].empty_label = "Nobody"

    def _phone(self, name: str, required: bool) -> str:
        value = self.cleaned_data.get(name, "")
        if not value and not required:
            return ""
        try:
            return normalize_line(value)
        except InvalidPhoneError as error:
            raise forms.ValidationError(str(error)) from None

    def clean_number(self) -> str:
        number = self._phone("number", True)
        clash = PhoneNumber.objects.filter(number=number)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("This number is already set up.")
        return number

    def clean_transfer_to(self) -> str:
        return self._phone("transfer_to", False)

    def save(self, commit=True):
        self.instance.organisation = self.scope.organisation
        return super().save(commit)


class CampaignForm(forms.ModelForm):
    days = forms.TypedMultipleChoiceField(
        choices=WEEKDAYS,
        coerce=int,
        widget=forms.CheckboxSelectMultiple,
        initial=[0, 1, 2, 3, 4, 5],
        label="Calling days",
    )

    class Meta:
        model = Campaign
        fields = (
            "name",
            "campus",
            "employee",
            "purpose",
            "window_start",
            "window_end",
            "days",
            "max_attempts",
            "retry_after_minutes",
            "daily_limit",
            "cooldown_days",
        )
        labels = {  # noqa: RUF012
            "employee": "AI employee",
            "window_start": "Calls from",
            "window_end": "Calls until",
            "max_attempts": "Attempts per person",
            "retry_after_minutes": "Wait before trying again (minutes)",
            "daily_limit": "Most calls per day",
            "cooldown_days": "Skip people called by another campaign in the last (days)",
        }
        widgets = {  # noqa: RUF012
            "window_start": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "window_end": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        }

    def __init__(self, *args, scope, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.scope = scope
        self.fields["campus"].queryset = scope.campuses().filter(is_active=True)
        self.fields["campus"].empty_label = None
        self.fields["employee"].queryset = _employees(scope).filter(
            live_version__isnull=False
        )
        self.fields["employee"].empty_label = None
        self.fields["employee"].help_text = "Only published employees can be chosen."
        self.fields["max_attempts"].widget.attrs.update(min=1, max=5)
        if not self.instance.pk:
            self.initial.setdefault("window_start", time(10, 0))
            self.initial.setdefault("window_end", time(18, 0))

    def clean(self):
        data = super().clean()
        _window(self, "window_start", "window_end")
        attempts = data.get("max_attempts")
        if attempts is not None and not 1 <= attempts <= 5:
            self.add_error("max_attempts", "Between 1 and 5 attempts.")
        if (
            data.get("retry_after_minutes") is not None
            and data["retry_after_minutes"] < 30
        ):
            self.add_error("retry_after_minutes", "Wait at least 30 minutes.")
        if data.get("daily_limit") is not None and data["daily_limit"] < 1:
            self.add_error("daily_limit", "At least one call a day.")
        return data

    def save(self, commit=True):
        self.instance.organisation = self.scope.organisation
        return super().save(commit)


class AddContactsForm(forms.Form):
    source = forms.ChoiceField(
        label="Add",
        choices=(
            ("consented", "Every contact of the campus with permission to call"),
            ("import", "The contacts from one import"),
        ),
    )
    batch = forms.ModelChoiceField(
        queryset=ContactImport.objects.none(),
        required=False,
        label="Import",
        empty_label="Choose an import",
    )

    def __init__(self, *args, campaign, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["batch"].queryset = ContactImport.objects.filter(
            campus=campaign.campus
        )
        self.fields["batch"].label_from_instance = lambda batch: (
            f"{batch.created_at:%d %b %Y %H:%M}: "
            f"{batch.created} contacts, {batch.calling_purpose}"
        )

    def clean(self):
        data = super().clean()
        if data.get("source") == "import" and not data.get("batch"):
            self.add_error("batch", "Choose an import.")
        return data


class ConsentAttestForm(forms.Form):
    attested = forms.BooleanField(
        label="Everyone on this campaign agreed to be called by us about its purpose.",
    )
    note = forms.CharField(
        label="How they agreed (evidence)",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 2}),
    )


class LeadSourceForm(forms.ModelForm):
    class Meta:
        model = LeadSource
        fields = (
            "name",
            "campus",
            "employee",
            "consent_statement",
            "call_delay_minutes",
            "window_start",
            "window_end",
            "is_active",
        )
        labels = {  # noqa: RUF012
            "employee": "AI employee who calls new leads",
            "consent_statement": "What the form tells people about being called",
            "call_delay_minutes": "Call after (minutes)",
            "window_start": "Calls from",
            "window_end": "Calls until",
            "is_active": "Accept new leads",
        }
        help_texts = {  # noqa: RUF012
            "consent_statement": "For example: “By submitting, you agree that we may "
            "call you about your enquiry.” It is kept as each lead's evidence of "
            "permission to call.",
        }
        widgets = {  # noqa: RUF012
            "consent_statement": forms.Textarea(attrs={"rows": 3}),
            "window_start": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "window_end": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        }

    def __init__(self, *args, scope, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.scope = scope
        self.fields["campus"].queryset = scope.campuses().filter(is_active=True)
        self.fields["campus"].empty_label = None
        self.fields["employee"].queryset = _employees(scope)
        self.fields["employee"].empty_label = "Nobody yet"

    def clean_consent_statement(self) -> str:
        text = " ".join(self.cleaned_data["consent_statement"].split())
        if len(text) < 20:
            raise forms.ValidationError("Write out what the form says, in full.")
        return text

    def clean(self):
        data = super().clean()
        _window(self, "window_start", "window_end")
        return data

    def save(self, commit=True):
        self.instance.organisation = self.scope.organisation
        return super().save(commit)


class KnowledgeForm(forms.ModelForm):
    class Meta:
        model = KnowledgeItem
        fields = ("kind", "campus", "title", "text", "is_active")
        labels = {  # noqa: RUF012
            "kind": "Type",
            "campus": "Used on calls for",
            "text": "What the employee is told",
            "is_active": "In use",
        }
        widgets = {"text": forms.Textarea(attrs={"rows": 3})}  # noqa: RUF012

    def __init__(self, *args, scope, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.scope = scope
        self.fields["campus"].queryset = scope.campuses()
        self.fields["campus"].empty_label = "Every campus"

    def save(self, commit=True):
        self.instance.organisation = self.scope.organisation
        return super().save(commit)
