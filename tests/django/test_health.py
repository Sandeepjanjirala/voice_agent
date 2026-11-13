from unittest import mock

import pytest
from django.db import DatabaseError
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_health_reports_ok_without_authentication(client: Client) -> None:
    response = client.get(reverse("core:health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "django", "database": "ok"}


def test_health_url_is_stable(client: Client) -> None:
    assert reverse("core:health") == "/api/health/"


def test_health_reports_degraded_when_database_is_down(client: Client) -> None:
    with mock.patch(
        "apps.core.views.connection.cursor", side_effect=DatabaseError("down")
    ):
        response = client.get(reverse("core:health"))

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "service": "django",
        "database": "unavailable",
    }


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_health_is_read_only(client: Client, method: str) -> None:
    response = getattr(client, method)(reverse("core:health"))

    assert response.status_code == 405


def test_admin_requires_login(client: Client) -> None:
    response = client.get("/admin/")

    assert response.status_code == 302
    assert response["Location"].startswith("/admin/login/")
