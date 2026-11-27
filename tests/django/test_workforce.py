"""Dashboard shell, the Hire wizard, employee drafts, publishing and versions.

No test reaches a real LLM: the HTTP call is replaced, and a guard fails any
test that would open a network connection for drafting.
"""

import io
import json
import re
import urllib.error
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from apps.core.navigation import MODULES, SECTIONS
from apps.crm import crypto
from apps.telephony.models import AgentProfile, CallRecord
from apps.tenancy.models import Campus, Membership, Organisation
from apps.workforce import config, drafting, llm, publishing
from apps.workforce.models import Employee, EmployeeVersion
from voice_agent import profile as worker_profile

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
BRIEF = (
    "Call every parent who sends an admission enquiry, ask which class the "
    "child is joining and offer a campus visit."
)
AI_DRAFT = {
    "agent_name": "Meera",
    "role": "Admissions enquiry assistant",
    "greeting": "హలో అండి! నేను Meera, AI assistant ని. ఒక్క నిమిషం మాట్లాడవచ్చా?",
    "purpose": "Follow up on the parent's admission enquiry.",
    "steps": [
        {"name": "Confirm parent", "guidance": "Check you are speaking to them."},
        {"name": "Ask class", "guidance": "Ask which class the child is joining."},
        {"name": "Offer visit", "guidance": "Offer a campus visit."},
    ],
    "questions": ["Class", "Preferred campus"],
    "limits": "You cannot confirm an admission or quote fees.",
    "ending": "End once a visit preference is noted.",
    "strictness": 2,
    "open_questions": ["Fee structure", "Campus visit timings"],
}
DASHBOARD_URLS = [
    "home",
    "employees",
    "employee-hire",
    *MODULES,
]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, settings):
    cache.clear()
    settings.LLM_PROVIDER = "livekit"
    settings.LIVEKIT_API_KEY = "key"
    settings.LIVEKIT_API_SECRET = "secret-secret-secret-secret-secret-12"
    settings.LLM_MODEL = ""
    settings.EMPLOYEE_DRAFT_MODEL = ""

    def no_network(*args, **kwargs):
        raise AssertionError("a test tried to reach the LLM service")

    monkeypatch.setattr(llm.urllib.request, "urlopen", no_network)


def organisation() -> Organisation:
    """The organisation these tests work in, with one campus."""
    found, created = Organisation.objects.get_or_create(name="Test Organisation")
    if created:
        Campus.objects.create(organisation=found, name="Main campus")
    return found


@pytest.fixture
def staff_client():
    """An organisation admin who is also Django staff (for the platform tools)."""
    user = get_user_model().objects.create_user(
        username="head", password="pw", is_staff=True
    )
    Membership.objects.create(
        user=user, organisation=organisation(), role=Membership.Role.ORG_ADMIN
    )
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def plain_client():
    user = get_user_model().objects.create_user(username="plain", password="pw")
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def ai(monkeypatch):
    """Drafting answers with AI_DRAFT and records what it was asked."""
    asked = []

    def complete(system, user, **kwargs):
        asked.append((system, user))
        return dict(AI_DRAFT)

    monkeypatch.setattr(llm, "complete_json", complete)
    return asked


def make_employee(**overrides) -> Employee:
    fields = {
        "organisation": organisation(),
        "name": "Meera",
        "role": "Admissions enquiry assistant",
        "brief": BRIEF,
        "draft": config.normalize(AI_DRAFT),
        **overrides,
    }
    return Employee.objects.create(**fields)


# --- configuration schema ------------------------------------------------------


def test_normalize_clamps_sizes_and_drops_unknown_keys():
    result = config.normalize(
        {
            "agent_name": "  Meera   Devi " + "x" * 100,
            "greeting": "g" * 999,
            "steps": [{"name": "n" * 99, "guidance": "g" * 999}] * 20
            + ["not a step", {"name": "", "guidance": ""}],
            "questions": ["Class", "Class", {"label": "Campus"}, "", 7],
            "strictness": 99,
            "open_questions": ["a"] * 50,
            "api_key": "must not survive",
        }
    )
    assert "api_key" not in result
    assert result["agent_name"].startswith("Meera Devi")
    assert len(result["agent_name"]) == config.LIMITS["agent_name"]
    assert len(result["greeting"]) == config.LIMITS["greeting"]
    assert len(result["steps"]) == config.MAX_STEPS
    assert len(result["steps"][0]["guidance"]) == config.LIMITS["step_guidance"]
    assert [q["key"] for q in result["questions"]] == [
        "class",
        "class_2",
        "campus",
        "7",
    ]
    assert result["strictness"] == 5
    assert len(result["open_questions"]) == config.MAX_OPEN_QUESTIONS


@pytest.mark.parametrize("junk", [None, "text", 3, [], {"steps": "x", "questions": 4}])
def test_normalize_survives_junk(junk):
    result = config.normalize(junk)
    assert result["steps"] == [] and result["questions"] == []
    assert result["strictness"] == config.DEFAULT_STRICTNESS
    assert config.normalize(result) == result


# --- LLM client ----------------------------------------------------------------


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_urlopen(monkeypatch, content, seen):
    def urlopen(request, timeout=None):
        seen.append(request)
        body = json.dumps({"choices": [{"message": {"content": content}}]})
        return _Reply(body.encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)


def test_complete_json_calls_the_gateway_with_a_signed_token(monkeypatch):
    seen = []
    _fake_urlopen(monkeypatch, '```json\n{"agent_name": "Meera"}\n```', seen)
    assert llm.complete_json("system", "user") == {"agent_name": "Meera"}
    request = seen[0]
    assert request.full_url == llm.LIVEKIT_INFERENCE_URL + "/chat/completions"
    token = request.get_header("Authorization").removeprefix("Bearer ")
    assert token.count(".") == 2 and "secret" not in token
    sent = json.loads(request.data)
    assert sent["model"] == llm.DEFAULT_MODELS["livekit"]
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]


@pytest.mark.parametrize("content", ["no json here", "{broken", "[1, 2]", ""])
def test_complete_json_rejects_unusable_replies(monkeypatch, content):
    _fake_urlopen(monkeypatch, content, [])
    with pytest.raises(llm.LLMError):
        llm.complete_json("system", "user")


def test_complete_json_reports_http_and_network_failures(monkeypatch):
    def http_error(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 429, "busy", {}, None)

    monkeypatch.setattr(llm.urllib.request, "urlopen", http_error)
    with pytest.raises(llm.LLMError, match="429"):
        llm.complete_json("system", "user")

    def offline(request, timeout=None):
        raise OSError("no route")

    monkeypatch.setattr(llm.urllib.request, "urlopen", offline)
    with pytest.raises(llm.LLMError, match="could not be reached"):
        llm.complete_json("system", "user")


def test_llm_provider_settings(settings):
    assert llm.configured()
    settings.LIVEKIT_API_SECRET = ""
    assert not llm.configured()
    settings.LLM_PROVIDER = "openai"
    settings.OPENAI_API_KEY = ""
    assert not llm.configured()
    settings.OPENAI_API_KEY = "sk-test"
    assert llm._endpoint() == (llm.OPENAI_URL, "sk-test", "gpt-4.1-mini")
    settings.LLM_PROVIDER = "openai_compatible"
    settings.LLM_BASE_URL, settings.LLM_API_KEY = "http://plain.example/v1", "k"
    settings.EMPLOYEE_DRAFT_MODEL = "some-model"
    assert llm.configured()
    with pytest.raises(llm.LLMError, match="HTTPS"):
        llm.complete_json("system", "user")
    settings.LLM_PROVIDER = "something-else"
    assert not llm.configured()


def test_llm_settings_are_documented_in_env_example():
    documented = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("EMPLOYEE_DRAFT_MODEL", "LIVEKIT_INFERENCE_URL", "LLM_PROVIDER"):
        assert f"\n{name}=" in documented, name


# --- drafting --------------------------------------------------------------------


def test_draft_uses_the_llm_and_normalizes_its_answer():
    asked = []

    def complete(system, user, **kwargs):
        asked.append(user)
        return {**AI_DRAFT, "strictness": "4", "surprise": "dropped"}

    draft = drafting.draft_employee(BRIEF, "instant", "te-en", complete=complete)
    assert draft.source == Employee.DraftSource.AI and draft.notice == ""
    assert draft.config["strictness"] == 4 and "surprise" not in draft.config
    assert BRIEF in asked[0] and "Tenglish" in asked[0]


@pytest.mark.parametrize("language", Employee.Language.values)
@pytest.mark.parametrize("channel", Employee.Channel.values)
def test_draft_falls_back_to_a_template_that_states_no_facts(channel, language):
    def complete(system, user, **kwargs):
        raise llm.LLMError("down")

    draft = drafting.draft_employee(BRIEF, channel, language, complete=complete)
    assert draft.source == Employee.DraftSource.TEMPLATE
    assert "template" in draft.notice
    assert draft.config["greeting"] and draft.config["steps"]
    assert "AI assistant" in draft.config["greeting"]
    assert not any(char.isdigit() for char in draft.config["greeting"])


def test_an_incomplete_llm_answer_is_not_used():
    draft = drafting.draft_employee(
        BRIEF, "inbound", "en", complete=lambda *a, **k: {"agent_name": "Only"}
    )
    assert draft.source == Employee.DraftSource.TEMPLATE


def test_examples_are_valid_starting_points():
    assert len(drafting.EXAMPLES) >= 4
    for example in drafting.EXAMPLES:
        assert example["channel"] in Employee.Channel.values
        assert len(example["brief"]) >= 20


# --- access ---------------------------------------------------------------------


@pytest.mark.parametrize("name", DASHBOARD_URLS)
def test_dashboard_pages_need_a_staff_account(name, client, plain_client, staff_client):
    url = reverse(name)
    anonymous = client.get(url)
    assert anonymous.status_code == 302
    assert anonymous["Location"].startswith(reverse("login"))
    plain = plain_client.get(url)
    if name == "home":
        assert plain.status_code == 200
        assert 'id="no-organisation"' in plain.content.decode()
    else:
        assert plain.status_code == 403
    response = staff_client.get(url)
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]


def test_employee_pages_need_a_staff_account(client, plain_client):
    employee = make_employee()
    for name in ("employee-detail", "employee-redraft", "employee-delete"):
        url = reverse(name, args=[employee.pk])
        assert client.post(url).status_code == 302
        assert plain_client.post(url).status_code == 403
    assert Employee.objects.filter(pk=employee.pk).exists()


def test_changes_need_post_and_a_csrf_token(staff_client):
    employee = make_employee()
    for name in ("employee-redraft", "employee-delete"):
        assert staff_client.get(reverse(name, args=[employee.pk])).status_code == 405
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(get_user_model().objects.get(username="head"))
    for url in (
        reverse("employee-hire"),
        reverse("employee-detail", args=[employee.pk]),
        reverse("employee-delete", args=[employee.pk]),
    ):
        assert strict.post(url, {"brief": BRIEF}).status_code == 403
    assert Employee.objects.count() == 1


# --- shell -----------------------------------------------------------------------


def test_every_sidebar_entry_resolves_and_marks_the_current_page(staff_client):
    names = [name for _, entries in SECTIONS for name, _, _ in entries]
    assert len(names) == len(set(names))
    for name in names:
        reverse(name)
    for name, label in (
        ("employees", "AI employees"),
        ("make-calls", "Make calls"),
        ("leads", "Leads &amp; contacts"),
    ):
        html = staff_client.get(reverse(name)).content.decode()
        assert html.count('aria-current="page"') == 1
        current_link = re.search(
            r'<a\b[^>]*\baria-current="page"[^>]*>(.*?)</a>', html, re.S
        )
        assert current_link is not None
        assert strip_tags(current_link.group(1)).strip() == label


def test_planned_modules_say_plainly_that_they_are_not_built(staff_client):
    for slug, info in MODULES.items():
        html = staff_client.get(reverse(slug)).content.decode()
        assert "Not built yet" in html
        assert f"MILESTONE {info['milestone']}" in html
        main = re.search(r"<main\b[^>]*>(.*?)</main>", html, re.S)
        assert main is not None
        assert "<form" not in main.group(1)


def test_someone_outside_any_organisation_sees_no_sidebar(plain_client):
    html = plain_client.get(reverse("home")).content.decode()
    assert 'class="sidebar"' not in html
    assert "You are not in an organisation yet" in html
    for hidden in ("AI employees", "Make calls", "Voice studio"):
        assert hidden not in html


def test_the_address_on_its_own_opens_home(staff_client):
    response = staff_client.get("/")
    assert response.status_code == 302 and response["Location"] == reverse("home")


def test_the_login_page_has_no_sidebar(client):
    html = client.get(reverse("login")).content.decode()
    assert 'class="sidebar"' not in html and "Sign in" in html


# --- home ------------------------------------------------------------------------


def test_home_reports_real_numbers(staff_client):
    response = staff_client.get(reverse("home"))
    assert response.context["stats"] == {
        "employees": 0,
        "published": 0,
        "open_leads": 0,
        "tasks_due": 0,
        "calls_today": 0,
        "week_total": 0,
        "answer_rate": None,
        "response": None,
    }
    assert "Set one up" in response.content.decode()

    make_employee()
    now = timezone.now()
    # A call of another organisation, and one of none, are not counted.
    other = Organisation.objects.create(name="Someone else")
    CallRecord.objects.create(
        room="other",
        masked_number="+91********09",
        organisation=other,
        status="completed",
    )
    CallRecord.objects.create(
        room="nobody", masked_number="+91********08", status="completed"
    )
    CallRecord.objects.create(
        organisation=organisation(),
        room="r1",
        masked_number="+91********01",
        remote_encrypted=crypto.encrypt("+919876500001"),
        status="completed",
        answered_at=now,
        e2e_latency_p50=1.2,
    )
    CallRecord.objects.create(
        organisation=organisation(),
        room="r2",
        masked_number="+91********01",
        remote_encrypted=crypto.encrypt("+919876500001"),
        status="failed",
    )
    CallRecord.objects.create(
        organisation=organisation(),
        room="r3",
        masked_number="+91********01",
        status="completed",
        created_at=now - timedelta(days=30),
        answered_at=now,
    )
    response = staff_client.get(reverse("home"))
    stats = response.context["stats"]
    assert stats["employees"] == 1 and stats["week_total"] == 2
    assert stats["answer_rate"] == 50 and stats["response"] == 1.2
    html = response.content.decode()
    assert "+919876500001" in html, "the latest call's full number is listed"
    assert "+91********09" not in html and "+91********08" not in html
    done = {item["label"]: item["done"] for item in response.context["setup"]}
    assert done["First AI employee drafted"] is True


# --- hire wizard -------------------------------------------------------------------


def test_hiring_drafts_an_editable_employee(staff_client, ai):
    page = staff_client.get(reverse("employee-hire")).content.decode()
    assert "Describe the job" in page and "New enquiry" in page
    response = staff_client.post(
        reverse("employee-hire"),
        {"brief": f"  {BRIEF}  ", "channel": "instant", "language": "te-en"},
    )
    employee = Employee.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("employee-detail", args=[employee.pk])
    assert (employee.name, employee.role) == ("Meera", "Admissions enquiry assistant")
    assert employee.brief == BRIEF and employee.draft_source == "ai"
    assert employee.created_by.username == "head"
    assert employee.draft == config.normalize(AI_DRAFT)
    assert BRIEF in ai[0][1]
    editor = staff_client.get(response["Location"]).content.decode()
    assert "Meera is drafted" in editor
    assert "Ask class" in editor and "Fee structure" in editor
    assert "Not published yet" in editor and employee.status == "draft"


def test_hiring_falls_back_to_a_template_and_says_so(staff_client, monkeypatch):
    def down(*args, **kwargs):
        raise llm.LLMError("down")

    monkeypatch.setattr(llm, "complete_json", down)
    response = staff_client.post(
        reverse("employee-hire"),
        {"brief": BRIEF, "channel": "inbound", "language": "en"},
        follow=True,
    )
    employee = Employee.objects.get()
    assert employee.draft_source == "template"
    html = response.content.decode()
    assert "plain template" in html


@pytest.mark.parametrize(
    "data",
    [
        {"brief": "too short", "channel": "instant", "language": "te-en"},
        {"brief": BRIEF, "channel": "fax", "language": "te-en"},
        {"brief": BRIEF, "channel": "instant", "language": "xx"},
        {"brief": "x" * 2001, "channel": "instant", "language": "te-en"},
    ],
)
def test_hiring_rejects_bad_input_without_calling_the_llm(staff_client, ai, data):
    response = staff_client.post(reverse("employee-hire"), data)
    assert response.status_code == 200
    assert not Employee.objects.exists() and ai == []


def test_drafting_is_throttled_per_user(staff_client, ai):
    data = {"brief": BRIEF, "channel": "instant", "language": "te-en"}
    assert staff_client.post(reverse("employee-hire"), data).status_code == 302
    second = staff_client.post(reverse("employee-hire"), data)
    assert second.status_code == 200
    assert "wait a few seconds" in second.content.decode()
    assert Employee.objects.count() == 1 and len(ai) == 1


def test_a_brief_cannot_inject_markup(staff_client, ai):
    brief = '<script>alert(1)</script> call parents about "admissions" & visits'
    staff_client.post(
        reverse("employee-hire"),
        {"brief": brief, "channel": "instant", "language": "te-en"},
    )
    employee = Employee.objects.get()
    html = staff_client.get(
        reverse("employee-detail", args=[employee.pk])
    ).content.decode()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# --- editor ------------------------------------------------------------------------


def _editor_post(**overrides):
    data = {
        "name": "Lakshmi",
        "role": "Parent support assistant",
        "channel": "inbound",
        "language": "en",
        "greeting": "Hello, I am an AI assistant.",
        "purpose": "Help parents with questions.",
        "limits": "No fees.",
        "ending": "End politely.",
        "strictness": "4",
        "step_name": ["Second", "First", ""],
        "step_guidance": ["Do B.", "Do A.", ""],
        "question": ["Student class", "", "Call-back time"],
    }
    data.update(overrides)
    return data


def test_editor_saves_the_draft_in_page_order(staff_client):
    employee = make_employee()
    url = reverse("employee-detail", args=[employee.pk])
    response = staff_client.post(url, _editor_post(), follow=True)
    assert "Draft saved." in response.content.decode()
    employee.refresh_from_db()
    assert (employee.name, employee.channel, employee.language) == (
        "Lakshmi",
        "inbound",
        "en",
    )
    draft = employee.draft
    assert draft["agent_name"] == "Lakshmi" and draft["strictness"] == 4
    assert [s["name"] for s in draft["steps"]] == ["Second", "First"]
    assert [q["label"] for q in draft["questions"]] == [
        "Student class",
        "Call-back time",
    ]
    # What the editor does not show is kept.
    assert draft["open_questions"] == AI_DRAFT["open_questions"]


def test_editor_rejects_a_blank_name_and_keeps_what_was_typed(staff_client):
    employee = make_employee()
    url = reverse("employee-detail", args=[employee.pk])
    response = staff_client.post(url, _editor_post(name="", purpose="Typed purpose"))
    assert response.status_code == 200
    assert "Typed purpose" in response.content.decode()
    employee.refresh_from_db()
    assert employee.name == "Meera"
    assert employee.draft["purpose"] == AI_DRAFT["purpose"]


def test_editor_clamps_oversized_fields(staff_client):
    employee = make_employee()
    staff_client.post(
        reverse("employee-detail", args=[employee.pk]),
        _editor_post(
            greeting="g" * 5000,
            step_name=["s"] * 30,
            step_guidance=["g" * 5000] * 30,
        ),
    )
    employee.refresh_from_db()
    assert len(employee.draft["greeting"]) == config.LIMITS["greeting"]
    assert len(employee.draft["steps"]) == config.MAX_STEPS


def test_redraft_replaces_the_script_from_the_original_brief(staff_client, ai):
    employee = make_employee(name="Old", draft=config.normalize({"greeting": "old"}))
    response = staff_client.post(
        reverse("employee-redraft", args=[employee.pk]), follow=True
    )
    employee.refresh_from_db()
    assert employee.name == "Meera" and employee.draft == config.normalize(AI_DRAFT)
    assert BRIEF in ai[0][1]
    assert "Drafted again" in response.content.decode()


def test_delete_removes_the_employee(staff_client):
    employee = make_employee()
    response = staff_client.post(
        reverse("employee-delete", args=[employee.pk]), follow=True
    )
    assert not Employee.objects.exists()
    assert "Meera was deleted." in response.content.decode()
    assert (
        staff_client.get(reverse("employee-detail", args=[employee.pk])).status_code
        == 404
    )


def test_employee_list_filters(staff_client):
    make_employee()
    make_employee(name="Ravi", role="Campus reception", channel="inbound")
    html = staff_client.get(reverse("employees")).content.decode()
    assert "Meera" in html and "Ravi" in html
    html = staff_client.get(reverse("employees"), {"q": "recep"}).content.decode()
    assert "Ravi" in html and "Meera" not in html
    html = staff_client.get(
        reverse("employees"), {"channel": "instant"}
    ).content.decode()
    assert "Meera" in html and "Ravi" not in html
    html = staff_client.get(reverse("employees"), {"q": "zzz"}).content.decode()
    assert "No employee matches" in html


# --- publishing ------------------------------------------------------------------


def test_the_worker_and_django_agree_on_the_profile_format():
    assert publishing.WORKER_LIMITS == worker_profile._LIMITS
    assert publishing.PROFILE_KEY == worker_profile.PROFILE_KEY
    # The worker's section holds the instructions plus labels and caller context.
    assert publishing.instruction_budget() + 2048 <= worker_profile.PROFILE_BUDGET_BYTES


def test_a_publishable_script_reaches_the_worker_whole():
    AgentProfile.objects.create(
        name="School", is_active=True, business_name="Test School", business_info="9-4"
    )
    employee = make_employee()
    assert publishing.problems(employee.draft) == []
    # The draft as a call would carry it, with today's knowledge.
    payload = publishing._with_knowledge(
        publishing.worker_payload(employee.draft, employee.language), employee, None
    )
    metadata = {publishing.PROFILE_KEY: payload}
    parsed = worker_profile.parse_profile(json.dumps(metadata))
    # Nothing was clamped on the way.
    voice = payload.pop("voice")
    assert payload.pop("observability") is False and parsed.cloud_recording is False
    for field, value in payload.items():
        assert getattr(parsed, field) == value, field
    assert (parsed.speaker, parsed.pace, parsed.language) == (
        voice["speaker"],
        voice["pace"],
        voice["language"],
    )
    assert voice["language"] == "te-IN", "Telugu + English speaks Telugu"
    # The employee speaks for its organisation. Without knowledge entries the
    # facts never come from the installation's Agent profile, which another
    # organisation may have written.
    assert parsed.business_name == "Test Organisation"
    assert parsed.business_info == ""
    assert parsed.greeting == AI_DRAFT["greeting"]
    assert "2. Ask which class the child is joining." in parsed.objectives
    assert "Find out: Class, Preferred campus." in parsed.objectives
    assert "follow the caller" not in parsed.limitations.lower()
    section = worker_profile.profile_instructions(parsed)
    assert len(section.encode()) <= worker_profile.PROFILE_BUDGET_BYTES
    assert parsed.ending in section and parsed.objectives in section


def test_strictness_changes_what_the_worker_is_told():
    loose = publishing.worker_payload({**AI_DRAFT, "strictness": 1})
    strict = publishing.worker_payload({**AI_DRAFT, "strictness": 5})
    assert "loose guide" in loose["limitations"]
    assert "do not improvise" in strict["limitations"]
    assert (
        publishing.worker_payload({**AI_DRAFT, "strictness": 3})["limitations"]
        == AI_DRAFT["limits"]
    )


def test_problems_explain_what_blocks_a_call():
    assert len(publishing.problems({})) == 4
    long_steps = [{"name": "Step", "guidance": "g" * 600}] * 12
    reasons = publishing.problems({**AI_DRAFT, "steps": long_steps})
    assert any("bytes" in reason for reason in reasons)
    telugu = {
        **AI_DRAFT,
        "purpose": "తె" * 600,
        "limits": "తె" * 600,
        "ending": "తె" * 300,
    }
    reasons = publishing.problems(telugu)
    assert any("Telugu letters too many" in reason for reason in reasons)
    assert publishing.prompt_size(telugu) > publishing.instruction_budget()


def _publish(client, employee, **kwargs):
    return client.post(
        reverse("employee-publish", args=[employee.pk]), follow=True, **kwargs
    )


def test_publishing_creates_versions_and_calls_use_the_latest(staff_client):
    employee = make_employee()
    assert employee.status == "draft" and publishing.has_changes(employee)
    html = _publish(staff_client, employee).content.decode()
    assert "Version 1 of Meera is published." in html
    employee.refresh_from_db()
    first = employee.live_version
    assert (first.number, employee.taking_calls, employee.status) == (
        1,
        True,
        "published",
    )
    assert first.published_by.username == "head"
    assert first.payload == publishing.worker_payload(employee.draft, "te-en")
    assert not publishing.has_changes(employee)

    # Editing the draft changes nothing that is published.
    url = reverse("employee-detail", args=[employee.pk])
    staff_client.post(url, _editor_post(name="Meera", purpose="A new purpose."))
    employee.refresh_from_db()
    first.refresh_from_db()
    assert employee.live_version == first and publishing.has_changes(employee)
    assert first.config["purpose"] == AI_DRAFT["purpose"]
    assert "changes that are not published" in staff_client.get(url).content.decode()

    _publish(staff_client, employee)
    employee.refresh_from_db()
    assert employee.live_version.number == 2
    assert employee.live_version.config["purpose"] == "A new purpose."
    assert list(employee.versions.values_list("number", flat=True)) == [2, 1]


def test_publishing_is_refused_while_the_script_has_problems(staff_client):
    employee = make_employee(draft=config.normalize({"agent_name": "Meera"}))
    html = _publish(staff_client, employee).content.decode()
    assert "Not published." in html and "Write the opening line." in html
    employee.refresh_from_db()
    assert employee.live_version is None and not EmployeeVersion.objects.exists()
    page = staff_client.get(
        reverse("employee-detail", args=[employee.pk])
    ).content.decode()
    assert 'id="problems"' in page and "source=draft" not in page


def test_discard_and_restore_only_touch_the_draft(staff_client):
    employee = make_employee()
    _publish(staff_client, employee)
    url = reverse("employee-detail", args=[employee.pk])
    staff_client.post(url, _editor_post(name="Meera", purpose="Second purpose."))
    _publish(staff_client, employee)
    staff_client.post(url, _editor_post(name="Meera", purpose="Unpublished."))

    staff_client.post(reverse("employee-discard", args=[employee.pk]))
    employee.refresh_from_db()
    assert employee.draft["purpose"] == "Second purpose."
    assert not publishing.has_changes(employee)

    response = staff_client.post(
        reverse("employee-restore", args=[employee.pk, 1]), follow=True
    )
    assert "Version 1 is copied into the draft" in response.content.decode()
    employee.refresh_from_db()
    assert employee.draft["purpose"] == AI_DRAFT["purpose"]
    assert employee.live_version.number == 2 and publishing.has_changes(employee)
    assert (
        staff_client.post(
            reverse("employee-restore", args=[employee.pk, 9])
        ).status_code
        == 404
    )


def test_discard_needs_something_published(staff_client):
    employee = make_employee()
    response = staff_client.post(
        reverse("employee-discard", args=[employee.pk]), follow=True
    )
    assert "Nothing is published yet" in response.content.decode()


def test_the_switch_pauses_and_resumes_a_published_employee(staff_client):
    employee = make_employee()
    url = reverse("employee-switch", args=[employee.pk])
    response = staff_client.post(url, {"taking_calls": "on"}, follow=True)
    assert "Publish the employee before" in response.content.decode()
    employee.refresh_from_db()
    assert not employee.taking_calls

    _publish(staff_client, employee)
    staff_client.post(url, {"taking_calls": "off"})
    employee.refresh_from_db()
    assert employee.status == "paused" and employee.live_version is not None
    staff_client.post(url, {"taking_calls": "on"})
    employee.refresh_from_db()
    assert employee.status == "published"
    html = staff_client.get(reverse("employees"), {"status": "paused"}).content.decode()
    assert "No employee matches" in html


def test_publishing_endpoints_need_staff_and_post(client, plain_client, staff_client):
    employee = make_employee()
    for name, args in (
        ("employee-publish", [employee.pk]),
        ("employee-discard", [employee.pk]),
        ("employee-switch", [employee.pk]),
        ("employee-restore", [employee.pk, 1]),
    ):
        url = reverse(name, args=args)
        assert client.post(url).status_code == 302
        assert plain_client.post(url).status_code == 403
        assert staff_client.get(url).status_code == 405
    assert not EmployeeVersion.objects.exists()


def test_deleting_an_employee_removes_its_versions(staff_client):
    employee = make_employee()
    _publish(staff_client, employee)
    staff_client.post(reverse("employee-delete", args=[employee.pk]))
    assert not Employee.objects.exists() and not EmployeeVersion.objects.exists()
