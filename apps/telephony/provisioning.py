"""Creates and removes the inbound trunk and dispatch rule in LiveKit.

The only module that changes anything. Rules it keeps:

- A blocked plan is never provisioned (the request builders refuse).
- It creates what is missing and never updates or overwrites what exists.
- If the dispatch rule cannot be created, a trunk created in the same run is
  removed again, so a half-built setup is not left behind.
- A trunk that comes back without authentication is removed immediately.
- Teardown deletes only the resources this plan names, rule first.
"""

from dataclasses import dataclass, field

from livekit import api

from .conf import TelephonySettings
from .livekit_gateway import ReadOnlySipGateway, SipSnapshot, TelephonyGatewayError
from .masking import mask_phones_in
from .planning import (
    PlanBlockedError,
    TelephonyPlan,
    dispatch_rule_request,
    inbound_trunk_request,
)
from .reconcile import CREATE_DISPATCH, CREATE_TRUNK, reconcile


class ProvisioningRefusedError(RuntimeError):
    """Nothing was changed: the preconditions for a safe change were not met."""


class ProvisioningFailedError(RuntimeError):
    """A change was attempted and did not complete. The message says what remains."""


@dataclass
class Outcome:
    dry_run: bool
    created: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    would: list[str] = field(default_factory=list)
    trunk_id: str = ""
    rule_id: str = ""


class SipProvisioner(ReadOnlySipGateway):
    """Adds the four write calls to the read-only gateway."""

    async def _call(self, method: str, request, secret: str = ""):
        client = self._api_factory(self._url, self._api_key, self._api_secret)
        try:
            return await getattr(client.sip, method)(request)
        except Exception as error:
            detail = mask_phones_in(str(getattr(error, "message", "")))[:200]
            if secret:
                detail = detail.replace(secret, "***")
            code = getattr(error, "code", type(error).__name__)
            raise TelephonyGatewayError(
                f"LiveKit refused {method} ({code}). {detail}".strip()
            ) from None
        finally:
            await client.aclose()

    async def create_trunk(self, request: api.CreateSIPInboundTrunkRequest) -> str:
        created = await self._call(
            "create_inbound_trunk", request, secret=request.trunk.auth_password
        )
        return created.sip_trunk_id

    async def create_rule(self, request: api.CreateSIPDispatchRuleRequest) -> str:
        created = await self._call("create_dispatch_rule", request)
        return created.sip_dispatch_rule_id

    async def delete_rule(self, rule_id: str) -> None:
        await self._call(
            "delete_dispatch_rule",
            api.DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule_id),
        )

    async def delete_trunk(self, trunk_id: str) -> None:
        await self._call(
            "delete_trunk", api.DeleteSIPTrunkRequest(sip_trunk_id=trunk_id)
        )


async def _roll_back_trunk(gateway: SipProvisioner, trunk_id: str, why: str) -> str:
    try:
        await gateway.delete_trunk(trunk_id)
    except TelephonyGatewayError:
        return (
            f"{why} Rolling back trunk {trunk_id} ALSO FAILED: delete it by hand "
            "in LiveKit Cloud > Telephony > SIP trunks."
        )
    return f"{why} The trunk created in this run ({trunk_id}) was removed again."


async def apply_plan(
    plan: TelephonyPlan,
    settings: TelephonySettings,
    gateway: SipProvisioner,
    *,
    dry_run: bool,
) -> Outcome:
    if not plan.ready:
        codes = ", ".join(blocker.code for blocker in plan.blockers)
        raise ProvisioningRefusedError(f"The plan is blocked ({codes}).")

    state = reconcile(plan, await gateway.snapshot())
    if not state.safe_to_apply:
        raise ProvisioningRefusedError(
            "LiveKit already holds configuration that needs a person to review: "
            + " ".join(state.problems)
        )

    outcome = Outcome(dry_run=dry_run, trunk_id=state.trunk_id, rule_id=state.rule_id)
    if dry_run or state.nothing_to_do:
        outcome.would = [a for a in state.actions if a.startswith("create")]
        outcome.kept = [a for a in state.actions if a.startswith("keep")]
        return outcome

    created_trunk = False
    if CREATE_TRUNK in state.actions:
        outcome.trunk_id = await gateway.create_trunk(
            inbound_trunk_request(plan, settings)
        )
        outcome.created.append(f"inbound trunk {outcome.trunk_id}")
        created_trunk = True
    else:
        outcome.kept.append(f"inbound trunk {outcome.trunk_id}")

    if CREATE_DISPATCH in state.actions:
        try:
            outcome.rule_id = await gateway.create_rule(
                dispatch_rule_request(plan, trunk_id=outcome.trunk_id)
            )
        except (TelephonyGatewayError, PlanBlockedError) as error:
            message = f"Dispatch rule was not created: {error}"
            if created_trunk:
                message = await _roll_back_trunk(gateway, outcome.trunk_id, message)
            raise ProvisioningFailedError(message) from None
        outcome.created.append(f"dispatch rule {outcome.rule_id}")
    else:
        outcome.kept.append(f"dispatch rule {outcome.rule_id}")

    # Trust what LiveKit now reports, not what we asked for.
    after = await gateway.snapshot()
    trunk = next((t for t in after.trunks if t.id == outcome.trunk_id), None)
    if trunk is None or not trunk.authenticated:
        message = "The trunk did not come back authenticated."
        if created_trunk:
            if outcome.rule_id and CREATE_DISPATCH in state.actions:
                await gateway.delete_rule(outcome.rule_id)
            message = await _roll_back_trunk(gateway, outcome.trunk_id, message)
        raise ProvisioningFailedError(message)
    check = reconcile(plan, after)
    if not check.nothing_to_do:
        raise ProvisioningFailedError(
            "Created, but LiveKit reports differences from the plan. Nothing was "
            f"removed; review trunk {outcome.trunk_id} and rule {outcome.rule_id}: "
            + " ".join(check.problems)
        )
    return outcome


def _ours(plan: TelephonyPlan, snapshot: SipSnapshot):
    trunks = [
        trunk
        for trunk in snapshot.trunks
        if trunk.name == plan.trunk.name
        or any(number in trunk.numbers for number in plan.trunk.all_numbers)
    ]
    trunk_ids = {trunk.id for trunk in trunks}
    rules = [
        rule
        for rule in snapshot.rules
        if rule.name == plan.dispatch.name
        or (rule.trunk_ids and set(rule.trunk_ids) <= trunk_ids)
    ]
    return trunks, rules


async def teardown_plan(
    plan: TelephonyPlan,
    gateway: SipProvisioner,
    *,
    confirm_trunk_id: str,
    dry_run: bool,
) -> Outcome:
    """Works on a blocked plan too: removing a setup must always be possible."""
    trunks, rules = _ours(plan, await gateway.snapshot())
    if len(trunks) > 1:
        ids = ", ".join(trunk.id for trunk in trunks)
        raise ProvisioningRefusedError(
            f"More than one trunk matches this plan ({ids}). Remove them by hand."
        )
    trunk_id = trunks[0].id if trunks else ""
    outcome = Outcome(dry_run=dry_run, trunk_id=trunk_id)
    targets = [f"dispatch rule {rule.id}" for rule in rules]
    if trunk_id:
        targets.append(f"inbound trunk {trunk_id}")
    if dry_run or not targets:
        outcome.would = targets
        return outcome
    if trunk_id and confirm_trunk_id != trunk_id:
        raise ProvisioningRefusedError(
            f"--confirm-trunk-id must be {trunk_id}, the trunk this plan owns."
        )

    # Rules first: once they are gone no call is routed, whatever happens next.
    for rule in rules:
        await gateway.delete_rule(rule.id)
        outcome.deleted.append(f"dispatch rule {rule.id}")
    if trunk_id:
        await gateway.delete_trunk(trunk_id)
        outcome.deleted.append(f"inbound trunk {trunk_id}")
    return outcome
