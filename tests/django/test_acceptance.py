"""One synthetic admission journey; provider calls are intercepted.

Run the complete acceptance gate with scripts/acceptance.py. This test is
application integration evidence, not a live-provider or deployment certificate.
"""

import json

import pytest
from django.urls import reverse

from apps.calling import analysis
from apps.calling.models import CallTranscript
from apps.crm.models import FollowUp
from apps.integrations import transport
from apps.operations import tasks
from apps.operations.models import JobRun, UsageDay
from apps.telephony.models import CallRecord
from apps.workforce import llm, publishing
from apps.workforce.models import Employee, KnowledgeItem
from voice_agent.outbound import parse_dial_request
from voice_agent.profile import parse_profile

from .test_calling import BUSINESS_LINE, SCRIPT, TRANSCRIPT, canned, inbound_post, route
from .test_calls import env, gateway  # noqa: F401
from .test_tenancy_crm import ASHA, World


@pytest.mark.django_db
def test_education_acceptance_journey(gateway, monkeypatch):  # noqa: F811
    def refuse(*args, **kwargs):
        raise AssertionError("Acceptance must not contact a provider")

    monkeypatch.setattr(transport, "send", refuse)
    monkeypatch.setattr(llm, "complete_json", lambda *a, **kw: dict(SCRIPT))
    world = World()
    admin = world.client(world.admin_a)
    response = admin.post(
        reverse("employee-hire"),
        {
            "brief": "Help parents with admission enquiries and campus visits.",
            "channel": "instant",
            "language": "en",
        },
    )
    assert response.status_code == 302
    employee = Employee.objects.get()
    employee.draft["voice"] = {"speaker": "kavya", "pace": 0.9}
    employee.save()
    version = publishing.publish(employee, world.admin_a)
    employee.refresh_from_db()
    assert employee.taking_calls
    KnowledgeItem.objects.create(
        organisation=world.org_a,
        campus=world.north,
        title="Visits",
        text="Bring the enquiry reference to reception.",
    )
    lead = world.lead(world.admin_a, world.north, subject="Admission visit")
    url = reverse("lead-detail", args=[lead.pk])
    assert world.client(world.admin_b).get(url).status_code == 404
    other = world.lead(world.admin_a, world.south, number="+919876500002")
    assert (
        world.client(world.staff_north)
        .get(reverse("lead-detail", args=[other.pk]))
        .status_code
        == 404
    )

    route(world, employee, campus=world.north)
    payload = {"room": "acceptance-inbound", "called": BUSINESS_LINE, "caller": ASHA}
    assert inbound_post(payload, secret="invalid").status_code == 403
    response = inbound_post(payload)
    assert response.status_code == 200
    profile = parse_profile(json.dumps(response.json()))
    assert (profile.speaker, profile.pace) == ("kavya", 0.9)
    assert "Bring the enquiry reference" in json.dumps(response.json())
    record = CallRecord.objects.get(room=payload["room"])
    assert record.employee_version == version and record.lead == lead
    employee.draft["greeting"] = "Unpublished change"
    employee.save()
    assert inbound_post(payload).json() == response.json()

    assert (
        admin.post(
            reverse("lead-rehearse", args=[lead.pk]), {"employee": employee.pk}
        ).status_code
        == 302
    )
    assert len(gateway.dispatches) == 1
    assert parse_dial_request(gateway.dispatches[0].metadata).to == "+919876543210"
    assert ASHA not in gateway.dispatches[0].metadata

    monkeypatch.setattr(llm, "complete_json", canned())
    record.status = "completed"
    record.save()
    analysis.store_transcript(record, TRANSCRIPT[:2])
    tasks.analyse_call.apply(args=[str(record.pk)]).get(propagate=True)
    record.refresh_from_db()
    assert record.result.status == "done"
    assert FollowUp.objects.filter(lead=lead).count() == 1
    assert not CallTranscript.objects.filter(call=record).exists()

    tasks.usage_report.apply().get(propagate=True)
    assert JobRun.objects.filter(status="ok").exists()
    assert UsageDay.objects.filter(organisation=world.org_a).exists()
    assert (
        admin.get(reverse("conversation-detail", args=[record.pk])).status_code == 200
    )
