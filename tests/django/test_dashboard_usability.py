"""Workflow links respect permissions and point to sections that exist."""

import pytest
from django.urls import reverse

from apps.workforce import config, publishing
from apps.workforce.models import Employee

from .test_tenancy_crm import World

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    return World()


def employee_for(world):
    return Employee.objects.create(
        organisation=world.org_a,
        name="Service assistant",
        role="Customer follow-up",
        draft=config.normalize(
            {
                "agent_name": "Service assistant",
                "greeting": "Hello, may I ask about your service experience?",
                "purpose": "Collect feedback on a completed service.",
                "steps": [
                    {"name": "Feedback", "guidance": "Ask how the service went."}
                ],
                "ending": "Thank the customer and end the call.",
            }
        ),
    )


def test_staff_employee_list_offers_available_workflow_without_editor_links(world):
    staff = world.client(world.staff_north)
    empty = staff.get(reverse("employees")).content.decode()
    assert "Ask your organisation administrator to publish" in empty
    assert f'href="{reverse("employee-hire")}"' not in empty

    employee = employee_for(world)
    detail_url = reverse("employee-detail", args=[employee.pk])
    page = staff.get(reverse("employees")).content.decode()
    assert employee.name in page
    assert f'href="{detail_url}"' not in page
    assert f'href="{reverse("make-calls")}"' in page
    assert staff.get(detail_url).status_code == 403
    admin_page = world.client(world.admin_a).get(reverse("employees")).content.decode()
    assert f'href="{detail_url}"' in admin_page


def test_employee_history_navigation_appears_after_first_publish(world):
    employee = employee_for(world)
    admin = world.client(world.admin_a)
    url = reverse("employee-detail", args=[employee.pk])
    page = admin.get(url).content.decode()
    assert 'href="#versions"' not in page
    publishing.publish(employee, world.admin_a)
    page = admin.get(url).content.decode()
    assert 'href="#versions"' in page and 'id="versions"' in page


@pytest.mark.parametrize("published", [False, True])
def test_make_calls_guidance_respects_employee_management_permission(world, published):
    if published:
        publishing.publish(employee_for(world), world.admin_a)
    url = reverse("make-calls")
    hire_link = f'href="{reverse("employee-hire")}"'
    staff_page = world.client(world.staff_north).get(url).content.decode()
    assert hire_link not in staff_page
    assert "Ask your organisation administrator" in staff_page
    admin_page = world.client(world.admin_a).get(url).content.decode()
    assert hire_link in admin_page


def test_call_result_employee_link_respects_editor_permission(world):
    employee = employee_for(world)
    version = publishing.publish(employee, world.admin_a)
    call = world.call(world.org_a, world.north, employee_version=version)
    url = reverse("conversation-detail", args=[call.pk])
    employee_url = reverse("employee-detail", args=[employee.pk])
    staff_page = world.client(world.staff_north).get(url).content.decode()
    assert employee.name in staff_page
    assert f'href="{employee_url}"' not in staff_page
    admin_page = world.client(world.admin_a).get(url).content.decode()
    assert f'href="{employee_url}"' in admin_page
