"""Run the consolidated, synthetic platform acceptance gate.

Provider/Redis/production checks remain explicit manual gates in
docs/PILOT_READINESS.md. No live provider mode exists in this command.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    environment = dict(os.environ)
    environment.update(
        INTEGRATIONS_LIVE_ENABLED="false",
        INTEGRATIONS_ALLOW_LOCAL="false",
        DJANGO_SETTINGS_MODULE="tests.django_settings",
    )
    commands = [
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-o",
            "cache_dir=.test-tmp/acceptance-cache",
        ],
        [sys.executable, "manage.py", "check"],
        [sys.executable, "manage.py", "makemigrations", "--check", "--dry-run"],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, env=environment, check=False)
        if result.returncode:
            return result.returncode
    print(
        "Synthetic acceptance passed. Live-provider and production gates remain pending."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
