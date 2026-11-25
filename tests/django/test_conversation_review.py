"""Any job, configured in the dashboard; and every call reviewable afterwards.

Three genuinely different employees (fee questions, parent feedback, checking
details on record) are set up through the editor and called through the signed
endpoints the voice worker uses. Each conversation is reported as the worker
reports it (times, cut-offs, speech-recognition confidence, tools, why it
ended), analysed against that employee's own job by a canned LLM, and read back
on the call page. The organisation's transcript setting decides what is kept.

No LiveKit, no provider, no network, no real numbers.
"""

# The fixtures imported from other test modules are named as test parameters.
# ruff: noqa: F811

import json
import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.calling import analysis
from apps.calling.models import CallResult, CallTranscript, PhoneNumber
from apps.telephony.models import CallRecord
from apps.tenancy.models import AuditEvent
from apps.workforce import config as employee_config
from apps.workforce import publishing
from apps.workforce.models import Employee
from voice_agent.profile import parse_profile, profile_instructions
from voice_agent.prompts import ROLE_WITH_TASK, build_instructions

from .test_calling import inbound_post
from .test_calls import env, post_event  # noqa: F401
from .test_tenancy_crm import ASHA, RAVI, World, assert_no_full_number, body

pytestmark = pytest.mark.django_db

FEES = {
    "agent_name": "Kavya",
    "role": "Fee desk",
    "greeting": "Hello, this is Kavya, the school's AI assistant for fees.",
    "purpose": "Answer parents' questions about school fees and due dates.",
    "success": "The parent knows the amount and due date they asked about.",
    "steps": [
        {"name": "Class", "guidance": "Ask which class the student is in."},
        {"name": "Answer", "guidance": "Answer the fee question from the information."},
    ],
    "questions": ["Student class", "Fee question"],
    "situations": [
        {
            "when": "they ask for a discount",
            "then": "say the accounts team will call back",
        },
    ],
    "escalation": "They dispute a payment already made.",
    "limits": "Never promise a discount or a late-fee waiver.",
    "ending": "End once the question is answered.",
    "outcomes": ["Fee question answered", "Accounts team to call back", "Wrong number"],
    "information": "Term 2 fee for Class 6: Rs 18,000.\nDue date: 15 January.",
    "actions": ["create_follow_up"],
}
FEEDBACK = {
    "agent_name": "Ravi",
    "role": "Feedback caller",
    "greeting": "Hello, I am Ravi, an AI assistant collecting feedback on the annual day.",
    "purpose": "Collect parents' feedback on the annual day event.",
    "success": "The parent gave a rating and one thing to improve.",
    "steps": [
        {"name": "Rate", "guidance": "Ask for a rating out of five."},
        {"name": "Improve", "guidance": "Ask what could be better next time."},
    ],
    "questions": ["Rating out of five", "What to improve"],
    "limits": "Do not argue with complaints.",
    "ending": "Thank them once both answers are given.",
    "outcomes": ["Feedback given", "Complaint to follow up", "Did not want to answer"],
}
VERIFY = {
    "agent_name": "Asha",
    "role": "Records check",
    "greeting": "Hello, I am Asha, an AI assistant checking the details we hold.",
    "purpose": "Confirm the student's home area and emergency contact on record.",
    "steps": [
        {"name": "Area", "guidance": "Ask them to confirm their home area."},
        {"name": "Contact", "guidance": "Ask the emergency contact's name."},
    ],
    "questions": ["Home area", "Emergency contact name"],
    "limits": "Never read the full address aloud.",
    "ending": "End once both are confirmed.",
    "outcomes": ["Details confirmed", "Details changed", "Could not verify"],
}
LINES = {
    "fees": "+914012345681",
    "feedback": "+914012345682",
    "verify": "+914012345683",
}


@pytest.fixture
def world(env):
    world = World()
    world.org_a.transcripts = "keep"
    world.org_a.save()
    return world


def hire(world, script, name):
    """Set up through the editor, as an admin does, then publish."""
    admin = world.client(world.admin_a)
    employee = Employee.objects.create(
        organisation=world.org_a,
        name=script["agent_name"],
        role=script["role"],
        language="en",
        brief=script["purpose"],
        draft=employee_config.normalize({}),
    )
    post = {
        "name": script["agent_name"],
        "role": script["role"],
        "channel": "inbound",
        "language": "en",
        "greeting": script["greeting"],
        "purpose": script["purpose"],
        "success": script.get("success", ""),
        "limits": script["limits"],
        "escalation": script.get("escalation", ""),
        "ending": script["ending"],
        "strictness": "3",
        "step_name": [s["name"] for s in script["steps"]],
        "step_guidance": [s["guidance"] for s in script["steps"]],
        "question": script["questions"],
        "situation_when": [s["when"] for s in script.get("situations", [])],
        "situation_then": [s["then"] for s in script.get("situations", [])],
        "outcome": script["outcomes"],
        "information": script.get("information", ""),
        "action": script.get("actions", []),
        "campus": [str(world.north.pk)],
    }
    saved = admin.post(
        reverse("employee-detail", args=[employee.pk]), post, follow=True
    )
    assert "Draft saved." in body(saved)
    published = admin.post(reverse("employee-publish", args=[employee.pk]), follow=True)
    assert "is published" in body(published)
    employee.refresh_from_db()
    PhoneNumber.objects.create(
        organisation=world.org_a,
        campus=world.north,
        number=LINES[name],
        employee=employee,
        routing_enabled=True,
    )
    return employee


def call_in(world, name, room, caller=ASHA):
    response = inbound_post({"room": room, "called": LINES[name], "caller": caller})
    assert response.status_code == 200
    return CallRecord.objects.get(room=room), parse_profile(json.dumps(response.json()))


def report(record, turns, end_reason="caller_hung_up", **fields):
    """The worker's "ended" report, signed, as voice_agent sends it."""
    assert (
        post_event(Client(), {"room": record.room, "event": "answered"}).status_code
        == 204
    )
    payload = {
        "room": record.room,
        "event": "ended",
        "outcome": "completed",
        "talk_seconds": 48,
        "end_reason": end_reason,
        "transcript": turns,
        **fields,
    }
    assert post_event(Client(), payload).status_code == 204
    record.refresh_from_db()
    return payload


class Analyst:
    """A canned LLM that records what it was asked."""

    def __init__(self, answer):
        self.answer, self.system, self.user = answer, "", ""

    def __call__(self, system, user, **kwargs):
        self.system, self.user = system, user
        return self.answer


# --- 1. any job, from the configuration form --------------------------------------------


@pytest.mark.parametrize(
    ("script", "name", "expected"),
    [
        (
            FEES,
            "fees",
            (
                "fee",
                "When they ask for a discount: say the accounts team",
                "dispute a payment",
            ),
        ),
        (FEEDBACK, "feedback", ("annual day", "rating and one thing to improve")),
        (VERIFY, "verify", ("emergency contact", "Never read the full address aloud")),
    ],
)
def test_each_employee_is_its_own_job_with_no_other_kind_of_call(
    world, script, name, expected
):
    employee = hire(world, script, name)
    config = employee.live_version.config
    assert config["outcomes"] == script["outcomes"]
    assert len(config["situations"]) == len(script.get("situations", []))
    record, profile = call_in(world, name, f"call-{name}-job")
    section = profile_instructions(profile)
    prompt = build_instructions("School", can_end_call=True, profile_section=section)
    for text in expected:
        assert text in prompt
    assert ROLE_WITH_TASK in prompt
    for word in ("appointment", "admission", "campus visit"):
        assert word not in prompt.lower(), word
    # Its own results travel with the call, for the analysis afterwards.
    assert record.context_snapshot["outcomes"] == script["outcomes"]


def test_the_information_it_may_give_is_looked_up_not_sent_every_turn(world):
    hire(world, FEES, "fees")
    _, profile = call_in(world, "fees", "call-fees-info")
    assert "Term 2 fee for Class 6: Rs 18,000." in profile.business_info
    assert "Rs 18,000" not in profile_instructions(profile)


def test_new_fields_count_towards_the_instruction_budget(world):
    small = employee_config.normalize(FEEDBACK)
    big = employee_config.normalize(
        {**FEEDBACK, "situations": [{"when": "w" * 150, "then": "t" * 350}] * 10}
    )
    assert publishing.prompt_size(big) > publishing.prompt_size(small) + 4000
    payload = publishing.worker_payload(big)
    assert payload["situations"].startswith("When www")


def test_knowledge_rules_reach_the_prompt(world):
    from apps.workforce.models import KnowledgeItem

    KnowledgeItem.objects.create(
        organisation=world.org_a,
        campus=world.north,
        kind=KnowledgeItem.Kind.RULE,
        title="Names",
        text="Never name a teacher.",
    )
    hire(world, FEEDBACK, "feedback")
    _, profile = call_in(world, "feedback", "call-feedback-rules")
    assert "- Business rules: Names: Never name a teacher." in profile_instructions(
        profile
    )


# --- 2. the conversation, analysed against the job ---------------------------------------

FEES_TURNS = [
    {
        "role": "assistant",
        "text": "Hello, this is Kavya, the school's AI assistant for fees.",
        "t": 0.4,
    },
    {
        "role": "user",
        "text": "What is the term two fee for class six?",
        "t": 3.9,
        "confidence": 0.93,
    },
    {"role": "tool", "name": "get_business_info", "ok": True, "t": 4.6},
    {
        "role": "assistant",
        "text": "The term two fee for class six is eighteen thousand rupees, due on fifteen January.",
        "t": 5.2,
    },
    {
        "role": "user",
        "text": "Can I get a discount? My number is 98765 43210.",
        "t": 11.0,
        "confidence": 0.41,
    },
    {
        "role": "assistant",
        "text": "The accounts team will call you back about a discount",
        "t": 12.1,
        "interrupted": True,
    },
    {"role": "user", "text": "Okay, thank you.", "t": 15.8},
    {"role": "assistant", "text": "Thank you, goodbye.", "t": 16.5},
]
FEES_ANSWER = {
    "summary": "The parent asked the term two fee for class six and was told it. They asked for a discount, which the accounts team will call back about.",
    "reason": "The parent wanted the term two fee and asked about a discount.",
    "topics": ["Term two fee", "Discount"],
    "info_given": ["The term two fee for class six is Rs 18,000, due 15 January."],
    "answers": {
        "student_class": "Class six",
        "fee_question": "Term two fee",
        "made_up": "x",
    },
    "details": [{"label": "Discount", "value": "Asked whether a discount is possible"}],
    "resolution": "partly",
    "result": "accounts team to call back",
    "outcome": "callback",
    "pending": ["Whether a discount is possible"],
    "next_step": "Accounts team to call the parent about a discount.",
    "follow_up_needed": True,
    "follow_up_reason": "The parent asked about a discount.",
    "adherence": "followed",
    "adherence_notes": [],
    "issues": [
        {
            "kind": "misunderstood",
            "detail": "Invented problem",
            "quote": "words nobody said on this call",
        },
        {
            "kind": "unanswered",
            "detail": "The discount was not answered on the call.",
            "quote": "Can I get a discount?",
        },
    ],
    "callback_requested": True,
    "callback_when": "",
    "opt_out_requested": False,
    "human_requested": False,
    "appointment_at": "2099-01-01T10:00",
}


def test_a_fee_call_is_analysed_against_its_own_job_and_shown_turn_by_turn(world):
    hire(world, FEES, "fees")
    record, _ = call_in(world, "fees", "call-fees-1")
    report(record, FEES_TURNS, e2e_latency_max=1.4, interruptions=1)
    assert record.end_reason == "caller_hung_up"

    stored = CallTranscript.objects.get(call=record).turns
    assert [turn["role"] for turn in stored] == [
        "assistant",
        "user",
        "tool",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert stored[4]["text"] == "Can I get a discount? My number is 98******10."
    assert stored[5]["interrupted"] is True and stored[4]["confidence"] == 0.41

    llm = Analyst(FEES_ANSWER)
    result = analysis.analyse(record, complete=llm)
    # The analysis was told this employee's job, and nothing about bookings.
    assert "Answer parents' questions about school fees" in llm.user
    assert (
        '"Fee question answered"; "Accounts team to call back"; "Wrong number"'
        in llm.user
    )
    assert "When they ask for a discount" in llm.user
    assert (
        "[0:11] Person (speech recognition unsure): Can I get a discount?" in llm.user
    )
    assert "(The AI employee used the get_business_info tool: worked)" in llm.user
    assert "AI employee (cut off by the person)" in llm.user
    assert "appointment_at" not in llm.system and "whatsapp" not in llm.system

    result.refresh_from_db()
    assert result.status == CallResult.Status.DONE
    assert result.result_label == "Accounts team to call back"
    assert result.resolution == "partly" and result.appointment_at is None
    assert result.answers == {
        "student_class": "Class six",
        "fee_question": "Term two fee",
    }
    assert result.pending == ["Whether a discount is possible"]
    assert result.follow_up_needed and "discount" in result.follow_up_reason
    kinds = {(issue["source"], issue["kind"]) for issue in result.issues}
    assert ("ai", "unanswered") in kinds
    assert ("data", "interrupted") in kinds and ("data", "unclear_speech") in kinds
    assert not any("Invented" in issue["detail"] for issue in result.issues)
    assert result.has_issues

    admin = world.client(world.admin_a)
    page = body(admin.get(reverse("conversation-detail", args=[record.pk])))
    for text in (
        "Accounts team to call back",
        "Partly resolved",
        "Needs follow-up",
        "The parent wanted the term two fee",
        "Next step:",
        "Student class",
        "Class six",
        "What the AI told them",
        "Whether a discount is possible",
        "The discount was not answered on the call.",
        "What speech recognition heard, not a recording",
        "What is the term two fee for class six?",
        "The term two fee for class six is eighteen thousand rupees",
        "cut off",
        "heard unclearly",
        "get_business_info",
        "0:11",
        "The person hung up",
        "Kept for 30 days",
    ):
        assert text in page, text
    assert "Invented problem" not in page
    assert_no_full_number(page)
    assert AuditEvent.objects.filter(action="call.transcript_viewed").count() == 1


@pytest.mark.parametrize(
    ("script", "name", "answer", "turns", "label", "follow_up"),
    [
        (
            FEEDBACK,
            "feedback",
            {
                "summary": "The parent rated the annual day four out of five and asked for more seating.",
                "reason": "Feedback call about the annual day.",
                "answers": {
                    "rating_out_of_five": "4",
                    "what_to_improve": "More seating",
                },
                "resolution": "resolved",
                "result": "Feedback given",
                "outcome": "information",
                "adherence": "followed",
            },
            [
                {
                    "role": "assistant",
                    "text": "How would you rate the annual day out of five?",
                    "t": 1.0,
                },
                {"role": "user", "text": "Four. We needed more chairs.", "t": 5.0},
                {
                    "role": "assistant",
                    "text": "Thank you, I will share that.",
                    "t": 6.0,
                },
            ],
            "Feedback given",
            False,
        ),
        (
            VERIFY,
            "verify",
            {
                "summary": "The person did not want to confirm details on the phone.",
                "reason": "Checking the details on record.",
                "resolution": "unresolved",
                "result": "a label the employee does not have",
                "pending": ["Emergency contact name"],
                "outcome": "other",
                "adherence": "partly",
                "adherence_notes": [
                    {
                        "note": "It read out the address, which its limits forbid.",
                        "quote": "Your address is 12 Lake Road",
                    }
                ],
            },
            [
                {
                    "role": "assistant",
                    "text": "Your address is 12 Lake Road, is that right?",
                    "t": 1.0,
                },
                {
                    "role": "user",
                    "text": "I will not confirm that on the phone.",
                    "t": 4.0,
                },
            ],
            "",
            True,
        ),
    ],
)
def test_other_jobs_get_their_own_results(
    world, script, name, answer, turns, label, follow_up
):
    hire(world, script, name)
    record, _ = call_in(world, name, f"call-{name}-1")
    report(record, turns, end_reason="agent_ended")
    llm = Analyst(answer)
    result = analysis.analyse(record, complete=llm)
    assert script["purpose"] in llm.user
    # Another employee's job never leaks into this one's analysis.
    assert not re.search(r"\bfees?\b", llm.user.lower())
    assert "appointment" not in llm.user.lower()
    assert result.result_label == label
    assert result.follow_up_needed is follow_up
    page = body(
        world.client(world.admin_a).get(
            reverse("conversation-detail", args=[record.pk])
        )
    )
    assert "The AI employee ended the call" in page
    for line in turns:
        assert line["text"] in page
    if name == "verify":
        assert (
            "It read out the address" in page
            and "Partly followed its instructions" in page
        )
        # The person's question was the last thing said and the AI ended the call.
        assert any(issue["kind"] == "unanswered" for issue in result.issues)


def test_the_analysis_never_asks_about_bookings_or_whatsapp(world):
    hire(world, FEEDBACK, "feedback")
    record, _ = call_in(world, "feedback", "call-feedback-book")
    # Even a call made when such actions existed gets the plain questions.
    record.context_snapshot["action_details"] = [{"kind": "book_appointment"}]
    system, _ = analysis.request_for(record, [])
    assert "appointment_at" not in system and "whatsapp_requested" not in system
    assert "{extra_keys}" not in system


# --- 3. what is kept, and who sees it ---------------------------------------------------


def test_summary_then_delete_shows_the_summary_and_says_the_conversation_went(world):
    world.org_a.transcripts = "analyse"
    world.org_a.save()
    hire(world, FEES, "fees")
    record, _ = call_in(world, "fees", "call-fees-analyse")
    payload = report(record, FEES_TURNS)
    analysis.analyse(record, complete=Analyst(FEES_ANSWER))
    result = CallResult.objects.get(call=record)
    assert not CallTranscript.objects.filter(call=record).exists()
    assert result.transcript_deleted_at is not None
    # The worker retries its report: a repeat never brings the conversation back.
    assert post_event(Client(), payload).status_code == 204
    assert not CallTranscript.objects.filter(call=record).exists()

    page = body(
        world.client(world.admin_a).get(
            reverse("conversation-detail", args=[record.pk])
        )
    )
    assert (
        "Accounts team to call back" in page
        and "Whether a discount is possible" in page
    )
    assert (
        "The conversation was deleted on" in page and "Privacy &amp; settings" in page
    )
    assert "What is the term two fee" not in page
    assert not AuditEvent.objects.filter(action="call.transcript_viewed").exists()


def test_off_keeps_nothing_and_says_so(world):
    world.org_a.transcripts = "off"
    world.org_a.save()
    hire(world, FEES, "fees")
    record, _ = call_in(world, "fees", "call-fees-off")
    report(record, FEES_TURNS)
    assert not CallTranscript.objects.filter(call=record).exists()
    page = body(
        world.client(world.admin_a).get(
            reverse("conversation-detail", args=[record.pk])
        )
    )
    assert "does not keep what was said on calls" in page
    assert record.end_reason == "caller_hung_up"


def test_other_organisations_and_campuses_never_see_the_call(world):
    hire(world, FEES, "fees")
    record, _ = call_in(world, "fees", "call-fees-scope")
    report(record, FEES_TURNS)
    url = reverse("conversation-detail", args=[record.pk])
    assert world.client(world.admin_b).get(url).status_code == 404
    CallRecord.objects.filter(pk=record.pk).update(campus=world.south)
    assert world.client(world.staff_north).get(url).status_code == 404
    assert not AuditEvent.objects.filter(action="call.transcript_viewed").exists()


def test_conversations_can_be_narrowed_to_what_needs_attention(world):
    hire(world, FEES, "fees")
    hire(world, FEEDBACK, "feedback")
    fees, _ = call_in(world, "fees", "call-fees-list")
    report(fees, FEES_TURNS)
    analysis.analyse(fees, complete=Analyst(FEES_ANSWER))
    calm, _ = call_in(world, "feedback", "call-feedback-list", caller=RAVI)
    report(
        calm, [{"role": "assistant", "text": "Thank you for the feedback.", "t": 1.0}]
    )
    analysis.analyse(
        calm,
        complete=Analyst(
            {
                "summary": "Feedback given.",
                "resolution": "resolved",
                "result": "Feedback given",
            }
        ),
    )
    admin = world.client(world.admin_a)

    def rows(**query):
        page = admin.get(reverse("conversations"), query).context["page"]
        return page.object_list

    assert [call.pk for call in rows(attention="follow_up")] == [fees.pk]
    assert [call.pk for call in rows(attention="issues")] == [fees.pk]
    assert [call.pk for call in rows(result="Feedback given")] == [calm.pk]
    listing = body(admin.get(reverse("conversations")))
    assert "Need follow-up" in listing and "Accounts team to call back" in listing
    assert "The parent wanted the term two fee" in listing
