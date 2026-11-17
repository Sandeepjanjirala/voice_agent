from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from apps.tenancy.models import Membership
from apps.workforce.models import Employee

from .importing import MAX_BYTES
from .models import Contact, FollowUp, Lead
from .phones import InvalidPhoneError, normalize

User = get_user_model()


def assignees(scope, campus=None):
    """People who can be given work: admins, and staff of the campus."""
    members = Membership.objects.filter(
        organisation=scope.organisation, user__is_active=True
    )
    if campus is not None:
        members = members.filter(Q(role=Membership.Role.ORG_ADMIN) | Q(campuses=campus))
    elif not scope.all_campuses:
        members = members.filter(
            Q(role=Membership.Role.ORG_ADMIN) | Q(campuses__in=scope.campus_ids)
        )
    return User.objects.filter(membership__in=members).distinct().order_by("username")


class _PhoneMixin:
    def clean_phone(self) -> str:
        try:
            return normalize(self.cleaned_data["phone"])
        except InvalidPhoneError as error:
            raise forms.ValidationError(str(error)) from None


class _ConsentMixin:
    def clean(self):
        data = super().clean()
        if (
            data.get("consent") == Contact.Consent.GRANTED
            and not data.get("consent_evidence", "").strip()
        ):
            self.add_error(
                "consent_evidence",
                "Say how and when the person agreed, so it can be shown later.",
            )
        return data


class LeadNewForm(_ConsentMixin, _PhoneMixin, forms.Form):
    campus = forms.ModelChoiceField(queryset=None, empty_label=None)
    name = forms.CharField(label="Contact name", max_length=120)
    phone = forms.CharField(
        label="Phone number",
        max_length=24,
        help_text="Stored encrypted. Only a masked form is shown afterwards.",
    )
    subject = forms.CharField(
        label="What it is about",
        max_length=160,
        help_text="For example the enquiry, product or service.",
    )
    details = forms.CharField(
        required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 3})
    )
    origin = forms.ChoiceField(
        label="Where the number came from",
        choices=[c for c in Contact.Origin.choices if c[0] != Contact.Origin.IMPORT],
    )
    calling_purpose = forms.CharField(
        label="What we may call about", max_length=200, required=False
    )
    consent = forms.ChoiceField(
        label="Permission to call", choices=Contact.Consent.choices[:3]
    )
    consent_evidence = forms.CharField(
        label="How and when they agreed",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    assigned_to = forms.ModelChoiceField(
        queryset=None, required=False, empty_label="Nobody yet"
    )
    employee = forms.ModelChoiceField(
        label="AI employee", queryset=None, required=False, empty_label="None"
    )

    def __init__(self, *args, scope, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["campus"].queryset = scope.campuses().filter(is_active=True)
        self.fields["assigned_to"].queryset = assignees(scope)
        self.fields["employee"].queryset = scope.limit_shared(Employee.objects.all())


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = (
            "name",
            "origin",
            "origin_detail",
            "calling_purpose",
            "preferred_language",
            "best_time",
        )
        labels = {  # noqa: RUF012
            "origin": "Where the number came from",
            "origin_detail": "Details of where it came from",
            "calling_purpose": "What we may call about",
            "best_time": "Best time to call",
        }


class ConsentForm(_ConsentMixin, forms.Form):
    consent = forms.ChoiceField(
        label="Permission to call", choices=Contact.Consent.choices
    )
    consent_evidence = forms.CharField(
        label="How and when (evidence)",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 2}),
    )


class ImportForm(forms.Form):
    campus = forms.ModelChoiceField(queryset=None, empty_label=None)
    origin = forms.ChoiceField(
        label="Where these numbers came from", choices=Contact.Origin.choices
    )
    calling_purpose = forms.CharField(
        label="What we may call these people about", max_length=200
    )
    text = forms.CharField(
        label="Contacts",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 8,
                "placeholder": "name,phone,subject\nAsha Rao,9876543210,Enquiry",
            }
        ),
        help_text='First line: column names, including "name" and "phone". '
        '"subject" is optional. Or choose a .csv file instead.',
    )
    file = forms.FileField(label="Or a .csv file", required=False)
    consent_attested = forms.BooleanField(
        required=False,
        label="Every person on this list agreed to be called by us about this.",
    )
    consent_evidence = forms.CharField(
        label="How they agreed (evidence)",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    create_leads = forms.BooleanField(
        required=False, initial=True, label="Create a lead for each new contact"
    )
    default_subject = forms.CharField(
        label="Lead subject when the list has none",
        max_length=160,
        required=False,
        initial="Imported enquiry",
    )

    def __init__(self, *args, scope, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["campus"].queryset = scope.campuses().filter(is_active=True)

    def clean(self):
        data = super().clean()
        upload = data.get("file")
        if upload:
            if upload.size > MAX_BYTES:
                self.add_error("file", "The file is too large.")
            else:
                try:
                    data["text"] = upload.read().decode("utf-8-sig")
                except UnicodeDecodeError:
                    self.add_error(
                        "file", "Save the file as CSV (UTF-8) and try again."
                    )
        if not data.get("text", "").strip() and not self.errors:
            self.add_error("text", "Paste the contacts or choose a file.")
        evidence = data.get("consent_evidence", "").strip()
        if data.get("consent_attested") and not evidence:
            self.add_error(
                "consent_evidence", "Say how these people agreed to be called."
            )
        if data.get("create_leads") and not data.get("default_subject", "").strip():
            data["default_subject"] = "Imported enquiry"
        return data


class FollowUpForm(forms.ModelForm):
    class Meta:
        model = FollowUp
        fields = ("title", "due_at", "assigned_to")
        labels = {"title": "What to do", "due_at": "By when"}  # noqa: RUF012
        widgets = {  # noqa: RUF012
            "due_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            )
        }

    def __init__(self, *args, scope, campus, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = assignees(scope, campus)
        self.fields["assigned_to"].empty_label = "Nobody yet"
        self.fields["due_at"].input_formats = ["%Y-%m-%dT%H:%M"]


class LeadUpdateForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = ("stage", "assigned_to", "employee")
        labels = {"employee": "AI employee"}  # noqa: RUF012

    def __init__(self, *args, scope, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = assignees(scope, self.instance.campus)
        self.fields["assigned_to"].empty_label = "Nobody yet"
        self.fields["employee"].queryset = scope.limit_shared(Employee.objects.all())
        self.fields["employee"].empty_label = "None"


class OptOutForm(_PhoneMixin, forms.Form):
    phone = forms.CharField(label="Phone number", max_length=24)
    reason = forms.CharField(max_length=200, required=False)
