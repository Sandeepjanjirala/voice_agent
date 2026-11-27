from django import forms

from apps.crm.phones import InvalidPhoneError, normalize_line
from apps.workforce.models import Employee, KnowledgeItem

from .models import PhoneNumber


def _employees(scope):
    return scope.limit_shared(Employee.objects.all())


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
            "campus": "Location",
            "employee": "AI employee who answers",
            "routing_enabled": "Answer calls to this number with the employee",
            "transfer_to": "Transfer number for callers who ask for a person",
        }
        help_texts = {  # noqa: RUF012
            "number": "The business line, e.g. +91 40 1234 5678.",
            "campus": "Calls to this number belong to this location and use its "
            "knowledge.",
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
        self.fields["campus"].empty_label = "Every location"

    def save(self, commit=True):
        self.instance.organisation = self.scope.organisation
        return super().save(commit)
