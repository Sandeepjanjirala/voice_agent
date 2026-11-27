"""CI credentials stay out of installers; missing secrets cannot imply a pass."""

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
BASH = shutil.which("bash")
if os.name == "nt":
    candidate = Path("C:/Program Files/Git/bin/bash.exe")
    BASH = str(candidate) if candidate.exists() else None


def _simulation_steps():
    source = (WORKFLOWS / "simulations.yml").read_text(encoding="utf-8")
    prefix, *steps = re.split(r"(?m)^    - ", source)
    assert "secrets." not in prefix, "Provider secrets must not be job-wide."
    return steps


def test_external_actions_are_immutable_and_checkout_does_not_keep_credentials():
    for workflow in WORKFLOWS.glob("*.yml"):
        source = workflow.read_text(encoding="utf-8")
        for action in re.findall(r"(?m)^\s+(?:- )?uses:\s+(\S+)", source):
            if not action.startswith("./"):
                assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", action)
        for step in re.split(r"(?m)^    - ", source)[1:]:
            if "uses: actions/checkout@" in step:
                assert "persist-credentials: false" in step


def test_provider_secrets_are_only_available_to_the_gate_and_simulation():
    allowed = {"Check the secrets", "Run simulations"}
    found = set()
    for step in _simulation_steps():
        if "secrets." in step and "${{ secrets." in step:
            name = step.splitlines()[0].removeprefix("name: ")
            assert name in allowed
            assert "uses:" not in step
            found.add(name)
    assert found == allowed
    assert "${{ secrets." not in (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")


@pytest.mark.skipif(BASH is None, reason="Bash is required")
@pytest.mark.parametrize(
    "event,available,exit_code,ready,notice",
    [
        ("push", False, 0, "ready=false", "::warning::Simulations did not run"),
        ("workflow_dispatch", False, 1, "", "::error::Simulations need"),
        ("push", True, 0, "ready=true", ""),
        ("workflow_dispatch", True, 0, "ready=true", ""),
    ],
)
def test_secret_gate_without_contacting_any_provider(
    tmp_path, event, available, exit_code, ready, notice
):
    step = next(
        s for s in _simulation_steps() if s.startswith("name: Check the secrets\n")
    )
    script = textwrap.dedent(step.split("      run: |\n", 1)[1])
    script = script.replace("${{ github.event_name }}", event)
    output = tmp_path / "outputs"
    output.touch()
    environment = os.environ.copy()
    environment["GITHUB_OUTPUT"] = output.as_posix()
    names = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "SARVAM_API_KEY")
    synthetic = "synthetic-ci-secret-must-not-appear-in-output"
    for name in names:
        environment[name] = synthetic if available else ""
    result = subprocess.run(
        [BASH, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == exit_code, result.stderr
    assert output.read_text().strip() == ready
    assert notice in result.stdout
    assert synthetic not in result.stdout + result.stderr + output.read_text()
