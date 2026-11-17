from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from .models import Campus, Membership, Organisation, RetentionPolicy

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
        fields = ("name",)


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
        fields = ("contact_days", "call_days", "audit_days")
        labels = {  # noqa: RUF012
            "contact_days": "Erase inactive contacts after (days)",
            "call_days": "Delete call records after (days)",
            "audit_days": "Delete access-log entries after (days)",
        }
