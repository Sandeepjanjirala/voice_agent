from django import forms

from .models import AgentProfile


class AgentProfileForm(forms.ModelForm):
    class Meta:
        model = AgentProfile
        fields = (
            "name",
            "business_name",
            "agent_name",
            "call_purpose",
            "greeting",
            "objectives",
            "limitations",
            "ending",
            "business_info",
        )
        labels = {  # noqa: RUF012
            "name": "Profile name",
            "call_purpose": "Why the agent is calling or answering",
            "objectives": "Conversation goals, in order",
            "limitations": "What the agent must not do or claim",
            "ending": "When and how to end the call",
            "business_info": "Business information",
        }
        help_texts = {  # noqa: RUF012
            "business_name": "Spoken to callers. Leave blank until you want it used.",
            "greeting": "The first thing said, exactly as written. Telugu words in "
            "Telugu script, English words in English letters.",
            "business_info": "Timings, address, services, fees. The agent looks "
            "this up when asked and may state only what is written here.",
        }
        widgets = {  # noqa: RUF012
            "call_purpose": forms.Textarea(attrs={"rows": 2}),
            "greeting": forms.Textarea(attrs={"rows": 2}),
            "objectives": forms.Textarea(attrs={"rows": 4}),
            "limitations": forms.Textarea(attrs={"rows": 3}),
            "ending": forms.Textarea(attrs={"rows": 2}),
            "business_info": forms.Textarea(attrs={"rows": 8}),
        }
