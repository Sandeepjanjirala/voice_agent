from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q

from .models import (
    Campus,
    Holiday,
    Membership,
    OpeningHours,
    Organisation,
    RetentionPolicy,
)

User = get_user_model()


class OrganisationSetupForm(forms.Form):
    name = forms.CharField(label="Organisation name", max_length=120)
    campus = forms.CharField(
        label="First campus or branch",
        max_length=120,
        help_text="You can add more afterwards.",
    )

    def clean_name(self) -> str:
        name = " ".join(self.cleaned_data["name"].split())
        if Organisation.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError("An organisation with this name exists.")
        return name


class OrganisationForm(forms.ModelForm):
    class Meta:
        model = Organisation
        fields = (
            "name",
            "transcripts",
            "cloud_observability",
            "cost_per_minute",
            "cost_per_message",
            "cost_per_analysis",
        )
        labels = {  # noqa: RUF012
            "transcripts": "What the AI employee hears on a call",
            "cloud_observability": "Let LiveKit Cloud keep call recordings, "
            "transcripts and traces for debugging",
            "cost_per_minute": "Your cost per talk minute (₹), for estimates",
            "cost_per_message": "Your cost per WhatsApp message (₹)",
            "cost_per_analysis": "Your cost per call summary (₹)",
        }
        help_texts = {  # noqa: RUF012
            "transcripts": "Needed for call summaries and outcomes. Numbers in it "
            "are masked. Recordings are not kept here.",
            "cloud_observability": "Off: every call asks LiveKit Cloud to keep "
            "nothing. On: if Agent observability is on for the LiveKit project, "
            "LiveKit keeps each call's audio, transcript and logs for 30 days in "
            "the project's data region (United States or EU, not India), with "
            "personal details redacted. Deleting data here does not delete "
            "LiveKit's copy.",
        }


class CampusForm(forms.ModelForm):
    class Meta:
        model = Campus
        fields = ("name", "code", "city", "is_active")
        labels = {"is_active": "In use", "code": "Short code"}  # noqa: RUF012

    def __init__(self, *args, organisation, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.organisation = organisation

    def clean_name(self) -> str:
        name = " ".join(self.cleaned_data["name"].split())
        clash = Campus.objects.filter(organisation=self.organisation, name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("This organisation has a campus of this name.")
        return name

    def save(self, commit=True):
        self.instance.organisation = self.organisation
        return super().save(commit)


class CampusCallsForm(forms.ModelForm):
    """How AI employees treat callers for this campus."""

    class Meta:
        model = Campus
        fields = ("slot_minutes", "after_hours_message", "escalation_user")
        labels = {  # noqa: RUF012
            "slot_minutes": "Length of one appointment (minutes)",
            "after_hours_message": "What the AI employee says when the campus is closed",
            "escalation_user": "Who is told when a caller asks for a person",
        }
        help_texts = {  # noqa: RUF012
            "after_hours_message": "Said before it takes a message. Leave empty "
            "for a plain line with the opening hours.",
            "escalation_user": "Without one, everyone who works in the campus is told.",
        }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        campus = self.instance
        self.fields["slot_minutes"].min_value = 5
        self.fields["slot_minutes"].max_value = 240
        self.fields["slot_minutes"].widget.attrs.update(min=5, max=240)
        # Admins, and staff who work in this campus.
        self.fields["escalation_user"].queryset = (
            User.objects.filter(
                Q(membership__organisation_id=campus.organisation_id)
                & (
                    Q(membership__role=Membership.Role.ORG_ADMIN)
                    | Q(membership__campuses=campus)
                ),
                is_active=True,
            )
            .distinct()
            .order_by("username")
        )
        self.fields["escalation_user"].empty_label = "Everyone in the campus"
        self.fields["escalation_user"].label_from_instance = lambda user: (
            user.get_full_name() or user.get_username()
        )

    def clean_slot_minutes(self) -> int:
        value = self.cleaned_data["slot_minutes"]
        if not 5 <= value <= 240:
            raise forms.ValidationError("Choose between 5 and 240 minutes.")
        return value

    def clean_after_hours_message(self) -> str:
        return " ".join(self.cleaned_data["after_hours_message"].split())


WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


class HoursForm(forms.Form):
    """Weekly opening hours: one row per weekday. No day ticked means the
    campus has no hours and is treated as always open."""

    def __init__(self, *args, campus, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.campus = campus
        current = {row.weekday: row for row in campus.hours.all()}
        for day, name in enumerate(WEEKDAY_NAMES):
            row = current.get(day)
            self.fields[f"open_{day}"] = forms.BooleanField(
                label=name, required=False, initial=row is not None
            )
            self.fields[f"opens_{day}"] = forms.TimeField(
                label=f"{name} opens",
                required=False,
                initial=row.opens if row else None,
                widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            )
            self.fields[f"closes_{day}"] = forms.TimeField(
                label=f"{name} closes",
                required=False,
                initial=row.closes if row else None,
                widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            )

    def rows(self):
        """(name, open, opens, closes) bound fields for the template."""
        return [
            (
                name,
                self[f"open_{day}"],
                self[f"opens_{day}"],
                self[f"closes_{day}"],
            )
            for day, name in enumerate(WEEKDAY_NAMES)
        ]

    def clean(self):
        data = super().clean()
        for day, name in enumerate(WEEKDAY_NAMES):
            if not data.get(f"open_{day}"):
                continue
            opens, closes = data.get(f"opens_{day}"), data.get(f"closes_{day}")
            if opens is None or closes is None:
                self.add_error(
                    f"opens_{day}", f"Give {name}'s opening and closing times."
                )
            elif opens >= closes:
                self.add_error(f"closes_{day}", f"{name} must close after it opens.")
        return data

    def save(self) -> int:
        """Replace the campus's hours. Returns the number of open days."""
        rows = [
            OpeningHours(
                campus=self.campus,
                weekday=day,
                opens=self.cleaned_data[f"opens_{day}"],
                closes=self.cleaned_data[f"closes_{day}"],
            )
            for day in range(7)
            if self.cleaned_data.get(f"open_{day}")
        ]
        OpeningHours.objects.filter(campus=self.campus).delete()
        OpeningHours.objects.bulk_create(rows)
        return len(rows)


class HolidayForm(forms.ModelForm):
    whole_organisation = forms.BooleanField(
        required=False, label="Closed at every campus"
    )

    class Meta:
        model = Holiday
        fields = ("date", "name")
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}  # noqa: RUF012
        labels = {"name": "Holiday"}  # noqa: RUF012

    def __init__(self, *args, campus, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.campus = campus

    def clean_name(self) -> str:
        return " ".join(self.cleaned_data["name"].split())

    def clean(self):
        data = super().clean()
        day = data.get("date")
        if day is not None:
            clash = Holiday.objects.filter(
                organisation_id=self.campus.organisation_id, date=day
            ).filter(Q(campus=self.campus) | Q(campus__isnull=True))
            if clash.exists():
                self.add_error("date", "This day is already a holiday here.")
        return data

    def save(self, commit=True):
        self.instance.organisation_id = self.campus.organisation_id
        self.instance.campus = (
            None if self.cleaned_data.get("whole_organisation") else self.campus
        )
        return super().save(commit)


class _MemberFields(forms.Form):
    role = forms.ChoiceField(choices=Membership.Role.choices)
    campuses = forms.ModelMultipleChoiceField(
        queryset=Campus.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Campus staff see only the campuses ticked here.",
    )

    def __init__(self, *args, organisation, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Only this organisation's campuses can be chosen, whatever is posted.
        self.fields["campuses"].queryset = Campus.objects.filter(
            organisation=organisation
        )

    def clean(self):
        data = super().clean()
        if data.get("role") == Membership.Role.CAMPUS_STAFF and not data.get(
            "campuses"
        ):
            self.add_error("campuses", "Choose at least one campus for campus staff.")
        return data


class MemberAddForm(_MemberFields):
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(label="Name", max_length=150, required=False)
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Give it to the person privately; they can change it later.",
    )
    field_order = ("username", "first_name", "password", "role", "campuses")

    def clean_username(self) -> str:
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is taken.")
        return username

    def clean_password(self) -> str:
        password = self.cleaned_data["password"]
        validate_password(password)
        return password


class MemberEditForm(_MemberFields):
    is_active = forms.BooleanField(label="Can sign in", required=False)
    field_order = ("role", "campuses", "is_active")


class RetentionForm(forms.ModelForm):
    class Meta:
        model = RetentionPolicy
        fields = ("contact_days", "call_days", "transcript_days", "audit_days")
        labels = {  # noqa: RUF012
            "contact_days": "Erase inactive contacts after (days)",
            "call_days": "Delete call records after (days)",
            "transcript_days": "Delete kept transcripts after (days)",
            "audit_days": "Delete access-log entries after (days)",
        }
