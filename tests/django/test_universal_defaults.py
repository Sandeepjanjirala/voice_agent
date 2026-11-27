"""Industry-neutral setup and imports, without contacting an AI provider."""

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.calling import analysis, calllists
from apps.core import assistant
from apps.crm import importing
from apps.tenancy.forms import MemberAddForm, OrganisationSetupForm
from apps.tenancy.models import Campus, Membership, Organisation
from apps.workforce import drafting, llm
from apps.workforce.models import Employee


@pytest.mark.parametrize("example", drafting.EXAMPLES, ids=lambda item: item["title"])
def test_hiring_examples_create_usable_industry_neutral_fallbacks(example):
    def unavailable(*args, **kwargs):
        raise llm.LLMError("Offline in this test")

    result = drafting.draft_employee(
        example["brief"],
        example["channel"],
        Employee.Language.ENGLISH,
        complete=unavailable,
    )
    assert result.source == Employee.DraftSource.TEMPLATE
    assert result.config["purpose"] == example["brief"]
    assert result.config["steps"] and result.config["greeting"]
    text = json.dumps(result.config).lower()
    for school_default in ("chaitanya", "admission", "parent", "student", "campus"):
        assert school_default not in text


@pytest.mark.parametrize(
    "brief",
    [
        "Answer customers asking about our repair service and collect their issue.",
        "Remind guests about their hotel booking and record arrival preferences.",
        "Help parents with school admissions using our approved school information.",
    ],
)
def test_drafting_passes_the_owners_industry_without_overriding_it(brief):
    seen = []

    def complete(system, user):
        seen.append((system, user))
        return drafting.template_draft(
            brief, Employee.Channel.INBOUND, Employee.Language.ENGLISH
        )

    result = drafting.draft_employee(
        brief, Employee.Channel.INBOUND, Employee.Language.ENGLISH, complete=complete
    )
    assert result.source == Employee.DraftSource.AI
    assert result.config["purpose"] == brief
    system, request = seen[0]
    assert brief in request and brief not in system
    assert "organisations in any industry" in system
    assert "Do not assume" in system


@pytest.mark.parametrize(
    "header",
    ["full name", "contact", "contact name", "customer", "customer name", "parent"],
)
@pytest.mark.django_db
def test_contact_and_call_imports_accept_generic_and_existing_name_columns(header):
    organisation = Organisation.objects.create(name="Service company")
    csv = f"{header},phone,subject\nAsha Rao,9876500001,Service enquiry\n"
    contact_rows = importing.analyse(organisation, csv)
    assert len(contact_rows) == 1
    assert contact_rows[0].status == importing.NEW
    assert contact_rows[0].name == "Asha Rao"
    assert contact_rows[0].subject == "Service enquiry"
    parsed = calllists.parse(upload=SimpleUploadedFile("contacts.csv", csv.encode()))
    assert parsed.rows == [("Asha Rao", "+919876500001")]
    assert not parsed.rejected


@pytest.mark.django_db
def test_location_labels_keep_existing_role_and_form_contracts():
    organisation = Organisation.objects.create(name="Service company")
    assert "location" in OrganisationSetupForm().fields["campus"].label
    form = MemberAddForm(organisation=organisation)
    assert form.fields["campuses"].label == "Locations"
    assert dict(form.fields["role"].choices)["campus_staff"] == "Location team member"
    assert Membership.Role.CAMPUS_STAFF == "campus_staff"
    assert Campus._meta.verbose_name_plural == "locations"


def test_workspace_help_and_call_review_do_not_assume_an_industry():
    assert "organisations in any industry" in assistant.SYSTEM
    assert "Locations & team" in assistant.SYSTEM
    assert "do not assume them" in analysis.SYSTEM_PROMPT
    for prompt in (assistant.SYSTEM, analysis.SYSTEM_PROMPT):
        assert "chaitanya" not in prompt.lower()
