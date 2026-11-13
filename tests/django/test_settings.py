import importlib

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def test_api_is_closed_by_default() -> None:
    assert settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == [
        "rest_framework.permissions.IsAuthenticated"
    ]


def test_debug_is_off_unless_enabled() -> None:
    assert settings.DEBUG is False


def test_missing_secret_key_fails_fast_outside_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config.settings as module

    # Stop the reload from refilling the key out of a developer's .env.local.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("DJANGO_SECRET_KEY", "")
    monkeypatch.setenv("DJANGO_DEBUG", "false")
    try:
        with pytest.raises(ImproperlyConfigured, match="DJANGO_SECRET_KEY"):
            importlib.reload(module)
    finally:
        monkeypatch.undo()
        importlib.reload(module)
