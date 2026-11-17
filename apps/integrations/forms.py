"""Admin forms for integrations and approved actions. Secrets are write-only:
the field is always empty on the page and a blank value keeps what is stored."""

import json
import re
import secrets as random

from django import forms

from apps.crm.models import Lead

from . import safety, schema
from . import secrets as stored
from .actions import INTEGRATION_KINDS, schema_for
from .models import ApprovedAction, Integration
from .webhooks import EVENTS

Kind = Integration.Kind

# (config fields, secret fields) per kind: name -> (label, help, required)
FIELDS = {
    Kind.CAMPUS_CALENDAR: ({}, {}),
    Kind.GOOGLE_CALENDAR: (
        {
            "calendar_id": (
                "Calendar ID",
                "For example the school's shared calendar address.",
                True,
            ),
            "client_id": ("OAuth client ID", "From your Google Cloud project.", True),
        },
        {
            "client_secret": ("OAuth client secret", "", True),
            "refresh_token": ("Refresh token", "With the calendar.events scope.", True),
        },
    ),
    Kind.WHATSAPP: (
        {
            "phone_number_id": (
                "Phone number ID",
                "From WhatsApp Manager, digits only.",
                True,
            ),
            "api_version": ("Graph API version", "For example v21.0.", False),
            "templates": (
                "Approved templates",
                "One template name per line, exactly as approved by Meta.",
                True,
            ),
        },
        {
            "access_token": (
                "Access token",
                "A system-user token for this WhatsApp account.",
                True,
            )
        },
    ),
    Kind.REST_API: (
        {
            "base_url": (
                "Base address",
                "https://… Requests can go only below this.",
                True,
            ),
            "auth_header": (
                "Authentication header",
                "For example Authorization. Leave blank for none.",
                False,
            ),
            "auth_scheme": ("Scheme", "For example Bearer.", False),
        },
        {"token": ("Token", "Sent in the authentication header.", False)},
    ),
    Kind.WEBHOOK: (
        {
            "url": (
                "Receiver address",
                "https://… Events are posted here, signed.",
                True,
            )
        },
        {},
    ),
}


class IntegrationForm(forms.Form):
    kind = forms.ChoiceField(choices=Integration.Kind.choices)
    name = forms.CharField(max_length=120)
    is_active = forms.BooleanField(required=False, initial=True, label="In use")
    events = forms.MultipleChoiceField(
        choices=sorted(EVENTS.items()),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Events to send (webhooks only)",
    )

    def __init__(self, *args, instance: Integration | None = None, **kwargs) -> None:
        self.instance = instance
        if instance is not None:
            initial = {
                "kind": instance.kind,
                "name": instance.name,
                "is_active": instance.is_active,
                "events": instance.config.get("events", []),
            }
            for name, value in instance.config.items():
                initial[f"config_{name}"] = (
                    "\n".join(value) if isinstance(value, list) else value
                )
            kwargs.setdefault("initial", initial)
        super().__init__(*args, **kwargs)
        if instance is not None:
            self.fields["kind"].disabled = True
        for config, secret_fields in FIELDS.values():
            for name, (label, help_text, _) in config.items():
                widget = (
                    forms.Textarea(attrs={"rows": 3}) if name == "templates" else None
                )
                self.fields[f"config_{name}"] = forms.CharField(
                    label=label,
                    help_text=help_text,
                    required=False,
                    max_length=2000,
                    widget=widget,
                )
            for name, (label, help_text, _) in secret_fields.items():
                kept = instance is not None and name in stored.read(instance)
                self.fields[f"secret_{name}"] = forms.CharField(
                    label=label,
                    required=False,
                    max_length=4000,
                    widget=forms.PasswordInput(
                        render_value=False, attrs={"autocomplete": "off"}
                    ),
                    help_text=("Stored. Leave blank to keep it. " if kept else "")
                    + help_text,
                )

    def clean(self):
        data = super().clean()
        kind = data.get("kind") or (self.instance.kind if self.instance else None)
        if kind not in FIELDS:
            return data
        config_fields, secret_fields = FIELDS[kind]
        config = {}
        for name, (label, _, required) in config_fields.items():
            value = (data.get(f"config_{name}") or "").strip()
            if required and not value:
                self.add_error(f"config_{name}", f"{label} is required.")
            if name == "templates":
                value = [line.strip() for line in value.splitlines() if line.strip()]
                if any(not re.fullmatch(r"[a-z0-9_]{1,512}", t) for t in value):
                    self.add_error(
                        f"config_{name}",
                        "Template names use lowercase letters, digits and _.",
                    )
            if value:
                config[name] = value
        for name in ("base_url", "url"):
            if config.get(name):
                try:
                    config[name] = safety.check_base_url(config[name])
                except safety.UnsafeRequestError as error:
                    self.add_error(f"config_{name}", str(error))
        phone_id = config.get("phone_number_id")
        if kind == Kind.WHATSAPP and phone_id and not phone_id.isdigit():
            self.add_error("config_phone_number_id", "Use digits only.")
        if kind == Kind.WEBHOOK:
            config["events"] = data.get("events") or []
            if not config["events"]:
                self.add_error("events", "Choose at least one event.")
        secrets_given = {
            name: data.get(f"secret_{name}") or "" for name in secret_fields
        }
        existing = stored.read(self.instance) if self.instance else {}
        for name, (label, _, required) in secret_fields.items():
            if required and not secrets_given[name] and name not in existing:
                self.add_error(f"secret_{name}", f"{label} is required.")
        data["config"], data["secrets"] = config, secrets_given
        return data

    def kind_fields(self, kind: str) -> list:
        """The bound fields that belong to one kind of integration."""
        config, secret_fields = FIELDS[kind]
        names = [f"config_{n}" for n in config] + [f"secret_{n}" for n in secret_fields]
        return [self[name] for name in names]

    def save(self, organisation, user) -> tuple[Integration, str]:
        """Save, returning the integration and a new webhook secret to show once."""
        data = self.cleaned_data
        integration = self.instance or Integration(
            organisation=organisation, kind=data["kind"], created_by=user
        )
        integration.name = data["name"]
        integration.is_active = data["is_active"]
        integration.config = data["config"]
        shown = ""
        values = dict(data["secrets"])
        if integration.kind == Kind.WEBHOOK and not stored.read(integration):
            shown = "whsec_" + random.token_urlsafe(32)
            values["signing_secret"] = shown
        stored.store(integration, values)
        integration.save()
        return integration, shown


class ActionForm(forms.ModelForm):
    method = forms.ChoiceField(
        choices=(("GET", "GET"), ("POST", "POST")), required=False
    )
    path = forms.CharField(
        required=False,
        max_length=200,
        help_text="Below the base address, e.g. /students/{admission_no}. "
        "{name} is filled from a validated input.",
    )
    body_fields = forms.CharField(
        required=False,
        max_length=300,
        help_text="Inputs sent as JSON (POST), comma-separated.",
    )
    response_fields = forms.CharField(
        required=False,
        max_length=300,
        help_text="Fields of the answer to keep, comma-separated. Others are dropped.",
    )
    after_call_outcomes = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        choices=(),
        label="Run after calls with these outcomes",
    )
    schema_text = forms.CharField(
        required=False,
        label="Inputs (JSON schema)",
        widget=forms.Textarea(attrs={"rows": 6, "spellcheck": "false"}),
        help_text='For example {"properties": {"admission_no": {"type": "string", '
        '"pattern": "[A-Z0-9]{6}"}}, "required": ["admission_no"]}',
    )
    template = forms.CharField(
        required=False, max_length=512, help_text="An approved template name."
    )
    language = forms.CharField(
        required=False,
        max_length=10,
        initial="en",
        help_text="Template language code, e.g. en or te.",
    )
    parameters = forms.CharField(
        required=False,
        max_length=200,
        help_text="Values for the template's {{1}}, {{2}}…, comma-separated, from: "
        "contact_name, lead_subject, campus_name, organisation_name.",
    )

    class Meta:
        model = ApprovedAction
        fields = ("label", "key", "kind", "integration", "is_enabled")
        labels = {"is_enabled": "Switched on", "key": "Short name"}  # noqa: RUF012
        help_texts = {  # noqa: RUF012
            "key": "Lowercase letters, digits and -. Employees refer to the action by it.",
        }

    def __init__(self, *args, organisation, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        from apps.calling.models import CallResult

        self.organisation = organisation
        self.fields["integration"].queryset = Integration.objects.filter(
            organisation=organisation
        ).exclude(kind=Kind.WEBHOOK)
        self.fields["integration"].required = False
        self.fields["after_call_outcomes"].choices = CallResult.Outcome.choices
        if not self.instance._state.adding:
            self.fields["key"].disabled = True
            self.fields["kind"].disabled = True
            settings = self.instance.settings
            for name in ("method", "path", "template", "language"):
                self.initial.setdefault(name, settings.get(name, ""))
            for name in ("body_fields", "response_fields", "parameters"):
                self.initial.setdefault(name, ", ".join(settings.get(name, [])))
            self.initial.setdefault(
                "after_call_outcomes", settings.get("after_call_outcomes", [])
            )
            if self.instance.input_schema:
                self.initial.setdefault(
                    "schema_text", json.dumps(self.instance.input_schema, indent=2)
                )

    def clean_key(self) -> str:
        key = self.cleaned_data["key"]
        clash = ApprovedAction.objects.filter(organisation=self.organisation, key=key)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("Another action has this short name.")
        return key

    def clean(self):
        data = super().clean()
        kind = data.get("kind") or self.instance.kind
        integration = data.get("integration")
        needed = INTEGRATION_KINDS.get(kind)
        if needed and (integration is None or integration.kind not in needed):
            names = ", ".join(Integration.Kind(k).label for k in needed)
            self.add_error(
                "integration", f"Choose an integration of this kind: {names}."
            )
        if not needed:
            data["integration"] = None

        def split(name: str) -> list[str]:
            return [p.strip() for p in (data.get(name) or "").split(",") if p.strip()]

        settings: dict = {}
        input_schema: dict = {}
        if kind == ApprovedAction.Kind.REST_REQUEST:
            try:
                input_schema = schema.check_schema(
                    json.loads(data.get("schema_text") or '{"properties": {}}')
                )
            except (ValueError, schema.SchemaError) as error:
                self.add_error(
                    "schema_text",
                    str(error)
                    if isinstance(error, schema.SchemaError)
                    else "This is not valid JSON.",
                )
            path = data.get("path") or ""
            if not path.startswith("/"):
                self.add_error("path", "The path must start with /.")
            missing = [
                p
                for p in safety.placeholders(path)
                if p not in input_schema.get("properties", {})
            ]
            if missing:
                self.add_error(
                    "path", f"Add these to the inputs: {', '.join(missing)}."
                )
            settings = {
                "method": data.get("method") or "GET",
                "path": path,
                "body_fields": split("body_fields"),
                "response_fields": split("response_fields"),
                "after_call_outcomes": data.get("after_call_outcomes") or [],
            }
        elif kind == ApprovedAction.Kind.WHATSAPP_TEMPLATE:
            settings = {
                "template": (data.get("template") or "").strip(),
                "language": (data.get("language") or "en").strip(),
                "parameters": split("parameters"),
            }
            allowed = {
                "contact_name",
                "lead_subject",
                "campus_name",
                "organisation_name",
            }
            if any(p not in allowed for p in settings["parameters"]):
                self.add_error("parameters", "Use only the listed values.")
            approved = (
                integration.config.get("templates") if integration else None
            ) or []
            if settings["template"] not in approved:
                self.add_error(
                    "template", "Choose a template on the integration's approved list."
                )
        data["settings"], data["input_schema"] = settings, input_schema
        return data

    def save(self, commit=True):
        self.instance.organisation = self.organisation
        self.instance.settings = self.cleaned_data["settings"]
        self.instance.input_schema = self.cleaned_data["input_schema"]
        return super().save(commit)


def run_form(action: ApprovedAction, data=None) -> forms.Form:
    """A form with one field per input of the action's schema."""
    form = forms.Form(data, prefix=f"run-{action.key}")
    rules = schema_for(action)
    for name, rule in rules.get("properties", {}).items():
        required = name in rules.get("required", [])
        label = name.replace("_", " ").capitalize()
        if rule.get("enum"):
            choices = [
                (v, Lead.Stage(v).label if v in Lead.Stage.values else v)
                for v in rule["enum"]
            ]
            field = forms.ChoiceField(choices=choices, label=label, required=required)
        elif rule["type"] == "boolean":
            field = forms.BooleanField(label=label, required=False)
        elif rule.get("format") == "date":
            field = forms.CharField(
                label=label,
                required=required,
                widget=forms.DateInput(attrs={"type": "date"}),
            )
        elif rule.get("format") == "date-time":
            field = forms.CharField(
                label=label,
                required=required,
                widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
            )
        else:
            field = forms.CharField(
                label=label, required=required, max_length=rule.get("maxLength", 500)
            )
        form.fields[name] = field
    return form
