"""Inbound telephony: `manage.py telephony plan|status|apply|teardown`.

`plan` uses no network. `status` only lists what LiveKit holds. `apply` and
`teardown` change LiveKit, and only with a typed confirmation and an explicit
flag; add `--dry-run` to see what they would do. Nothing here contacts the SIP
provider or buys anything.
"""

import asyncio
import json

from django.conf import settings as django_settings
from django.core.management.base import BaseCommand, CommandError

from apps.telephony.conf import TelephonyConfigError, load_telephony_settings
from apps.telephony.livekit_gateway import ReadOnlySipGateway, TelephonyGatewayError
from apps.telephony.masking import mask_phone, mask_phones_in
from apps.telephony.planning import TelephonyPlan, build_plan
from apps.telephony.provisioning import (
    Outcome,
    ProvisioningFailedError,
    ProvisioningRefusedError,
    SipProvisioner,
    apply_plan,
    teardown_plan,
)
from apps.telephony.reconcile import reconcile


class Command(BaseCommand):
    help = "Plan, inspect, create and remove the inbound telephony setup."

    def add_arguments(self, parser):
        actions = parser.add_subparsers(dest="action", required=True)

        plan = actions.add_parser("plan", help="Show the intended setup. No network.")
        plan.add_argument("--json", action="store_true", dest="as_json")

        actions.add_parser(
            "status", help="List LiveKit SIP configuration and compare. Read-only."
        )

        apply = actions.add_parser(
            "apply", help="Create the missing trunk and dispatch rule in LiveKit."
        )
        apply.add_argument("--dry-run", action="store_true")
        apply.add_argument("--confirm-number", default="")
        apply.add_argument("--yes-change-livekit", action="store_true")

        teardown = actions.add_parser(
            "teardown", help="Delete this plan's trunk and dispatch rule from LiveKit."
        )
        teardown.add_argument("--dry-run", action="store_true")
        teardown.add_argument("--confirm-trunk-id", default="")
        teardown.add_argument("--yes-delete-from-livekit", action="store_true")

    def handle(self, *args, action, **options):
        try:
            self.telephony = load_telephony_settings()
        except TelephonyConfigError as error:
            raise CommandError(f"Telephony configuration error: {error}") from None
        self.plan = build_plan(
            self.telephony, agent_name=django_settings.LIVEKIT_AGENT_NAME
        )
        getattr(self, f"_{action}")(**options)

    def _credentials(self) -> dict[str, str]:
        url = django_settings.LIVEKIT_URL
        key = django_settings.LIVEKIT_API_KEY
        secret = django_settings.LIVEKIT_API_SECRET
        if not (url and key and secret):
            raise CommandError(
                "LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set."
            )
        return {"url": url, "api_key": key, "api_secret": secret}

    def _run(self, operation) -> Outcome:
        try:
            return asyncio.run(operation)
        except ProvisioningRefusedError as error:
            raise CommandError(
                f"Refused, nothing was changed: {mask_phones_in(str(error))}"
            ) from None
        except (ProvisioningFailedError, TelephonyGatewayError) as error:
            raise CommandError(mask_phones_in(str(error))) from None

    # --- read-only ---------------------------------------------------------

    def _plan(self, *, as_json=False, **_):
        if as_json:
            self.stdout.write(json.dumps(self.plan.as_dict(), indent=2))
            return
        self._write_plan(self.plan)

    def _status(self, **_):
        gateway = ReadOnlySipGateway(**self._credentials())
        try:
            snapshot = asyncio.run(gateway.snapshot())
        except TelephonyGatewayError as error:
            raise CommandError(str(error)) from None

        write = self.stdout.write
        write("LiveKit SIP configuration (read-only; nothing was changed)")
        write(f"  Inbound trunks: {len(snapshot.trunks)}")
        for trunk in snapshot.trunks:
            numbers = ", ".join(mask_phone(n) for n in trunk.numbers) or "any number"
            guard = "authenticated" if trunk.authenticated else "NOT AUTHENTICATED"
            write(
                f"    {trunk.id}  {trunk.name}  {numbers}  {guard}  "
                f"media encryption {trunk.media_encryption}"
            )
        write(f"  Dispatch rules: {len(snapshot.rules)}")
        for rule in snapshot.rules:
            scope = ", ".join(rule.trunk_ids) or "ALL TRUNKS"
            agents = ", ".join(rule.agent_names) or "no agent"
            write(f"    {rule.id}  {rule.name}  {rule.kind}  {scope}  -> {agents}")

        result = reconcile(self.plan, snapshot)
        write("")
        write("Compared with the plan")
        for problem in result.problems:
            write(f"  PROBLEM  {mask_phones_in(problem)}")
        if result.nothing_to_do:
            write("  The planned trunk and dispatch rule already exist and match.")
        else:
            write(f"  An apply would: {', '.join(result.actions)}")
        write(
            f"  Plan blockers: {len(self.plan.blockers)} "
            "(run `telephony plan` for the list)"
        )

    # --- changes LiveKit ----------------------------------------------------

    def _apply(
        self, *, dry_run=False, confirm_number="", yes_change_livekit=False, **_
    ):
        if not self.plan.ready:
            codes = ", ".join(blocker.code for blocker in self.plan.blockers)
            raise CommandError(f"Refusing to apply: the plan is blocked ({codes}).")
        if not dry_run:
            if confirm_number != self.telephony.inbound_number:
                raise CommandError(
                    "Refusing to apply: --confirm-number must be the full inbound "
                    "number."
                )
            if not yes_change_livekit:
                raise CommandError("Refusing to apply: pass --yes-change-livekit.")

        gateway = SipProvisioner(**self._credentials())
        outcome = self._run(
            apply_plan(self.plan, self.telephony, gateway, dry_run=dry_run)
        )
        write = self.stdout.write
        if dry_run:
            write("Dry run: nothing was changed.")
            write(f"  Would do: {', '.join(outcome.would) or 'nothing'}")
            write(f"  Already in place: {', '.join(outcome.kept) or 'nothing'}")
            return
        if not outcome.created:
            write("Nothing to do: the trunk and dispatch rule already exist and match.")
            return
        write(f"Created: {', '.join(outcome.created)}")
        if outcome.kept:
            write(f"Kept: {', '.join(outcome.kept)}")
        write(f"Point the provider at: {self.plan.provider_destination_uri}")
        write(
            f"To remove: telephony teardown --confirm-trunk-id {outcome.trunk_id} "
            "--yes-delete-from-livekit"
        )

    def _teardown(
        self, *, dry_run=False, confirm_trunk_id="", yes_delete_from_livekit=False, **_
    ):
        if not dry_run:
            if not confirm_trunk_id:
                raise CommandError(
                    "Refusing to tear down: --confirm-trunk-id must name the trunk."
                )
            if not yes_delete_from_livekit:
                raise CommandError(
                    "Refusing to tear down: pass --yes-delete-from-livekit."
                )
        gateway = SipProvisioner(**self._credentials())
        outcome = self._run(
            teardown_plan(
                self.plan, gateway, confirm_trunk_id=confirm_trunk_id, dry_run=dry_run
            )
        )
        write = self.stdout.write
        if dry_run:
            write("Dry run: nothing was changed.")
            write(f"  Would delete: {', '.join(outcome.would) or 'nothing'}")
        elif outcome.deleted:
            write(f"Deleted: {', '.join(outcome.deleted)}")
        else:
            write("Nothing to delete: LiveKit holds no trunk or rule for this plan.")

    # --- output -------------------------------------------------------------

    def _write_plan(self, plan: TelephonyPlan) -> None:
        write = self.stdout.write
        trunk, dispatch = plan.trunk, plan.dispatch
        write("Inbound telephony plan (offline; nothing was contacted or changed)")
        write(f"  Provider:               {plan.provider_label}")
        write(f"  Inbound number:         {mask_phone(trunk.number)}")
        write(f"  LiveKit SIP endpoint:   {plan.sip_endpoint or '(not set)'}")
        write(
            f"  Provider sends calls to: {plan.provider_destination_uri or '(not set)'}"
        )
        write("")
        write(f"  Inbound trunk:          {trunk.name}")
        if trunk.auth_mode == "digest":
            username = trunk.auth_username or "(not set)"
            write(f"    Authentication:       username and password ({username})")
        else:
            addresses = ", ".join(trunk.allowed_addresses) or "(not set)"
            write(f"    Authentication:       address allow-list ({addresses})")
        write(f"    Media encryption:     {trunk.media_encryption}")
        write(f"    Ringing timeout:      {trunk.ringing_timeout_seconds} s")
        write(f"    Max call duration:    {trunk.max_call_duration_seconds} s")
        write("")
        write(f"  Dispatch rule:          {dispatch.name}")
        write(
            f"    Rooms:                {dispatch.room_prefix}<our number>_<random>, "
            "one per call"
        )
        write(f"    Agent:                {dispatch.agent_name}")
        hidden = "hidden from the worker" if dispatch.hide_caller_number else "visible"
        write(f"    Caller number:        {hidden}")
        write("")
        for warning in plan.warnings:
            write(f"  NOTE     {warning}")
        if plan.ready:
            write("  READY: no blockers.")
            return
        write(f"  NOT READY: {len(plan.blockers)} blocker(s)")
        for blocker in plan.blockers:
            write(f"    [{blocker.waiting_on}] {blocker.code}: {blocker.message}")
