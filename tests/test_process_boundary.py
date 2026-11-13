"""Guards the architectural rule: the voice worker and Django stay separate.

The worker must not import Django (no ORM in the audio loop) and Django must
not import the voice stack (no voice loop inside a request or task).
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

WORKER_DIRS = ("voice_agent",)
DJANGO_DIRS = ("config", "apps")
WORKER_FORBIDDEN = ("django", "rest_framework", "config", "apps", "celery")
DJANGO_FORBIDDEN = ("livekit.agents", "livekit.plugins", "voice_agent")
BANNED_EVERYWHERE = ("fastapi", "sqlalchemy")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _violations(dirs: tuple[str, ...], forbidden: tuple[str, ...]) -> list[str]:
    problems = []
    for directory in dirs:
        for path in sorted((ROOT / directory).rglob("*.py")):
            for module in sorted(_imports(path)):
                if any(module == f or module.startswith(f + ".") for f in forbidden):
                    problems.append(f"{path.relative_to(ROOT)} imports {module}")
    return problems


def test_python_sources_were_found() -> None:
    for directory in WORKER_DIRS + DJANGO_DIRS:
        assert list((ROOT / directory).rglob("*.py")), directory


def test_voice_worker_does_not_import_django() -> None:
    assert _violations(WORKER_DIRS, WORKER_FORBIDDEN) == []


def test_django_does_not_import_the_voice_stack() -> None:
    assert _violations(DJANGO_DIRS, DJANGO_FORBIDDEN) == []


@pytest.mark.parametrize("banned", BANNED_EVERYWHERE)
def test_no_fastapi_or_sqlalchemy(banned: str) -> None:
    assert _violations(WORKER_DIRS + DJANGO_DIRS, (banned,)) == []
    assert banned not in (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
