from django import forms

from .models import Employee

MIN_BRIEF = 20


class BriefForm(forms.Form):
    """Step one of hiring: the job in the owner's own words."""

    brief = forms.CharField(
        label="What should this employee do?",
        min_length=MIN_BRIEF,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 5}),
        error_messages={
            "min_length": "Describe the job in a sentence or two, so the draft "
            "has something to work from.",
        },
    )
    channel = forms.ChoiceField(
        label="What kind of calls?", choices=Employee.Channel.choices
    )
    language = forms.ChoiceField(
        label="Language of the calls", choices=Employee.Language.choices
    )

    def clean_brief(self) -> str:
        return " ".join(self.cleaned_data["brief"].split())


class EmployeeForm(forms.ModelForm):
    """The employee's own fields; its script fields are read in the view."""

    class Meta:
        model = Employee
        fields = ("name", "role", "channel", "language")
