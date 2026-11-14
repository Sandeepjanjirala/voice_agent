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
from apps.telephony.outbound import (
    OutboundGateway,
    apply_outbound,
    build_outbound_plan,
    load_outbound_settings,
    place_test_call,
    teardown_outbound,
)
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

        actions.add_parser(
            "outbound-plan", help="Show the outbound test setup. No network."
        )
        out_apply = actions.add_parser(
            "outbound-apply", help="Create the outbound trunk in LiveKit."
        )
        out_apply.add_argument("--dry-run", action="store_true")
        out_apply.add_argument("--yes-change-livekit", action="store_true")
        out_teardown = actions.add_parser(
            "outbound-teardown", help="Delete the outbound trunk from LiveKit."
        )
        out_teardown.add_argument("--dry-run", action="store_true")
        out_teardown.add_argument("--yes-delete-from-livekit", action="store_true")

        call = actions.add_parser(
            "call", help="Place ONE real call to the allowed test number."
        )
        call.add_argument("--to", required=True)
        call.add_argument("--yes-place-one-call", action="store_true")
        call.add_argument("--no-wait", action="store_true")

    def handle(self, *args, action, **options):
        try:
            if action.startswith("outbound") or action == "call":
                self.outbound = load_outbound_settings()
                self.outbound_plan = build_outbound_plan(self.outbound)
            else:
                self.telephony = load_telephony_settings()
                self.plan = build_plan(
                    self.telephony, agent_name=django_settings.LIVEKIT_AGENT_NAME
                )
        except TelephonyConfigError as error:
            raise CommandError(f"Telephony configuration error: {error}") from None
        getattr(self, f"_{action.replace('-', '_')}")(**options)

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

    # --- outbound test ------------------------------------------------------

    def _outbound_plan(self, **_):
        plan = self.outbound_plan
        write = self.stdout.write
        write("Outbound test plan (offline; nothing was contacted or changed)")
        write(f"  Trunk:                 {plan.trunk_name}")
        write(f"  Provider SIP domain:   {plan.address or '(not set)'}")
        write(
            f"  Authentication:        username and password ({plan.auth_username or '(not set)'})"
        )
        write(f"  Caller ID:             {mask_phone(plan.caller_id)}")
        write(
            f"  Signalling / media:    {plan.transport} / encryption {plan.media_encryption}"
        )
        write(
            f"  Originates from:       LiveKit region for '{plan.destination_country}'"
        )
        write(f"  Only number dialled:   {mask_phone(plan.allowed_number)}")
        write(
            f"  Limits:                ring {self.outbound.ringing_timeout_seconds} s, "
            f"call {self.outbound.max_call_duration_seconds} s, no retries"
        )
        for warning in plan.warnings:
            write(f"  NOTE     {warning}")
        if plan.ready:
            write("  READY: no blockers.")
            return
        write(f"  NOT READY: {len(plan.blockers)} blocker(s)")
        for blocker in plan.blockers:
            write(f"    [{blocker.waiting_on}] {blocker.code}: {blocker.message}")

    def _outbound_apply(self, *, dry_run=False, yes_change_livekit=False, **_):
        if not dry_run and not yes_change_livekit:
            raise CommandError("Refusing to apply: pass --yes-change-livekit.")
        gateway = OutboundGateway(**self._credentials())
        action, trunk_id = self._run(
            apply_outbound(self.outbound_plan, self.outbound, gateway, dry_run=dry_run)
        )
        messages = {
            "kept": f"Nothing to do: outbound trunk {trunk_id} already exists and matches.",
            "would_create": "Dry run: would create the outbound trunk. Nothing was changed.",
            "created": f"Created outbound trunk {trunk_id}.",
        }
        self.stdout.write(messages[action])

    def _outbound_teardown(self, *, dry_run=False, yes_delete_from_livekit=False, **_):
        if not dry_run and not yes_delete_from_livekit:
            raise CommandError("Refusing to tear down: pass --yes-delete-from-livekit.")
        gateway = OutboundGateway(**self._credentials())
        ids = self._run(teardown_outbound(self.outbound_plan, gateway, dry_run=dry_run))
        verb = "Would delete" if dry_run else "Deleted"
        self.stdout.write(f"{verb}: {', '.join(ids) or 'nothing'}")

    def _call(self, *, to="", yes_place_one_call=False, no_wait=False, **_):
        if not yes_place_one_call:
            raise CommandError(
                "Refusing to call: this places a real, billed phone call. Pass "
                "--yes-place-one-call."
            )
        gateway = OutboundGateway(**self._credentials())
        room = self._run(
            place_test_call(
                self.outbound_plan,
                self.outbound,
                gateway,
                to=to.strip().replace(" ", ""),
                agent_name=django_settings.LIVEKIT_AGENT_NAME,
            )
        )
        write = self.stdout.write
        write(f"Asked {django_settings.LIVEKIT_AGENT_NAME} to call {mask_phone(to)}.")
        write(f"  Room: {room}")
        write("  The voice worker must be running; its log shows the outcome.")
        if no_wait:
            return
        self._run(self._follow(gateway, room))

    async def _follow(self, gateway: OutboundGateway, room: str) -> None:
        """Print call status changes until the call is active or over. Read-only."""
        last, seen_room = None, False
        for _ in range((self.outbound.ringing_timeout_seconds + 30) // 2):
            status = await gateway.call_status(room)
            if status is None:
                if seen_room:
                    self.stdout.write("  Call ended (room closed).")
                    return
            else:
                seen_room = True
                if status and status != last:
                    self.stdout.write(f"  Call status: {status}")
                    last = status
                if status == "active":
                    self.stdout.write("  Answered. Hang up on your phone to end it.")
                    return
            await asyncio.sleep(2)
        self.stdout.write("  No answer was observed. Check the worker log.")

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
