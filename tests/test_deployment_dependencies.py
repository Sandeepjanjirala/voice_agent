"""Keep both runtime dependency graphs faithful to the process boundary."""

import runpy
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _runtime_packages(group):
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = {}
    for package in lock["package"]:
        packages.setdefault(package["name"], []).append(package)
    project = packages["telugu-ai-calling-agent"][0]
    pending = [*project["dependencies"], *project["dev-dependencies"][group]]
    seen = set()
    while pending:
        dependency = pending.pop()
        name = dependency["name"]
        extras = tuple(sorted(dependency.get("extra", [])))
        identity = (name, extras)
        if identity in seen:
            continue
        seen.add(identity)
        # Visit every platform variant: neither Linux nor development platforms
        # may accidentally pull the opposite process's runtime into this image.
        for package in packages[name]:
            pending.extend(package.get("dependencies", []))
            for extra in extras:
                pending.extend(package.get("optional-dependencies", {}).get(extra, []))
    return {name for name, _ in seen}


def test_backend_runtime_keeps_control_plane_and_excludes_audio_models():
    packages = _runtime_packages("backend")
    assert {"django", "celery", "gunicorn", "whitenoise", "livekit-api"} <= packages
    assert not packages & {"livekit-agents", "livekit", "av", "numpy", "torch"}
    assert not any(name.startswith("livekit-plugins-") for name in packages)
    assert "livekit-local-inference" not in packages
    assert not packages & {"pytest", "ruff"}


def test_voice_runtime_excludes_backend_and_dev_tools():
    packages = _runtime_packages("voice")
    assert {"livekit-agents", "livekit-plugins-sarvam", "livekit-api"} <= packages
    assert not packages & {
        "django",
        "celery",
        "gunicorn",
        "whitenoise",
        "pytest",
        "ruff",
    }


def test_default_development_commands_keep_both_runtime_groups():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert {"backend", "voice", "dev"} <= set(project["tool"]["uv"]["default-groups"])


def test_backend_image_project_label_matches_scoped_cleanup():
    dockerfile = (ROOT / "deploy/Dockerfile.web").read_text(encoding="utf-8")
    shared = (ROOT / "deploy/compose.shared.yaml").read_text(encoding="utf-8")
    cleanup = (ROOT / "scripts/cleanup.sh").read_text(encoding="utf-8")
    assert "ARG APP_PROJECT=voice-agent" in dockerfile
    assert 'io.vaani.project="${APP_PROJECT}"' in dockerfile
    assert "APP_PROJECT: ${COMPOSE_PROJECT_NAME:-voice-agent}" in shared
    assert '"label=io.vaani.project=$PROJECT"' in cleanup


def test_single_worker_gthread_avoids_reproduced_recycling_disconnect(monkeypatch):
    monkeypatch.delenv("GUNICORN_WORKERS", raising=False)
    configuration = runpy.run_path(str(ROOT / "deploy/gunicorn.conf.py"))
    assert configuration["workers"] == 1
    assert configuration["worker_class"] == "gthread"
    assert configuration["max_requests"] == configuration["max_requests_jitter"] == 0
