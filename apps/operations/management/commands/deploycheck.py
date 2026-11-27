"""Private, machine-readable deployment checks; never call a provider."""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.operations import deployment


class Command(BaseCommand):
    help = "Check local deployment dependencies and work in progress as JSON."
    requires_system_checks = ()

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="append",
            choices=("readiness", "storage", "worker", "beat", "inflight"),
            help="Repeat for multiple checks; defaults to readiness.",
        )
        parser.add_argument(
            "--prepare-storage",
            action="store_true",
            help="Create the private local recording directory if missing; check storage.",
        )
        parser.add_argument(
            "--require-idle",
            action="store_true",
            help="Fail when calls or important background work are outstanding.",
        )
        parser.add_argument(
            "--offline-workers",
            action="store_true",
            help="Only after workers stop: inspect DB and broker without worker RPC.",
        )

    def handle(self, *args, **options):
        names = options["check"] or (
            ["storage"] if options["prepare_storage"] else ["readiness"]
        )
        if options["require_idle"] and "inflight" not in names:
            raise CommandError("--require-idle needs --check inflight.")
        if options["offline_workers"] and "inflight" not in names:
            raise CommandError("--offline-workers needs --check inflight.")
        if options["prepare_storage"] and "storage" not in names:
            names.append("storage")
        checks = {}
        for name in dict.fromkeys(names):
            try:
                if name == "storage":
                    result = deployment.storage(prepare=options["prepare_storage"])
                elif name == "inflight":
                    result = deployment.inflight(
                        offline_workers=options["offline_workers"]
                    )
                    if options["require_idle"]:
                        result["ok"] = result["ok"] and result["idle"]
                else:
                    result = getattr(deployment, name)()
            except deployment.ProbeError as error:
                result = {"ok": False, "error": str(error)}
            except Exception as error:
                # A connection exception can contain its password/URL. Never print it.
                result = {"ok": False, "error": type(error).__name__}
            checks[name] = result
        ok = all(result["ok"] for result in checks.values())
        self.stdout.write(json.dumps({"ok": ok, "checks": checks}, sort_keys=True))
        if not ok:
            raise CommandError("Deployment checks failed; see the JSON checks above.")
