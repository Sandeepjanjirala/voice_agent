"""Compares the plan with what LiveKit holds and decides what `apply` may do.

Pure. The rule is: create what is missing, keep what already matches, and stop
for a person whenever something exists that differs, is duplicated or would
compete. Nothing is ever updated or deleted automatically.
"""

from dataclasses import dataclass

from .livekit_gateway import RuleView, SipSnapshot, TrunkView
from .planning import TelephonyPlan

CREATE_TRUNK = "create_trunk"
KEEP_TRUNK = "keep_trunk"
CREATE_DISPATCH = "create_dispatch_rule"
KEEP_DISPATCH = "keep_dispatch_rule"


@dataclass(frozen=True)
class Reconciliation:
    actions: tuple[str, ...]
    problems: tuple[str, ...]
    trunk_id: str = ""
    rule_id: str = ""

    @property
    def safe_to_apply(self) -> bool:
        return not self.problems

    @property
    def nothing_to_do(self) -> bool:
        return self.safe_to_apply and self.actions == (KEEP_TRUNK, KEEP_DISPATCH)


def _trunk_differences(plan: TelephonyPlan, trunk: TrunkView) -> list[str]:
    spec = plan.trunk
    checks = {
        "name": (spec.name, trunk.name),
        "numbers": (tuple(sorted(spec.all_numbers)), tuple(sorted(trunk.numbers))),
        "auth username": (spec.auth_username, trunk.auth_username),
        "allowed addresses": (
            tuple(sorted(spec.allowed_addresses)),
            tuple(sorted(trunk.allowed_addresses)),
        ),
        "media encryption": (spec.media_encryption, trunk.media_encryption),
        "ringing timeout": (
            spec.ringing_timeout_seconds,
            trunk.ringing_timeout_seconds,
        ),
        "max call duration": (
            spec.max_call_duration_seconds,
            trunk.max_call_duration_seconds,
        ),
    }
    return [field for field, (want, have) in checks.items() if want != have]


def _rule_differences(plan: TelephonyPlan, rule: RuleView, trunk_id: str) -> list[str]:
    spec = plan.dispatch
    checks = {
        "name": (spec.name, rule.name),
        "bound trunk": ((trunk_id,), rule.trunk_ids),
        "rule type": ("callee", rule.kind),
        "random room suffix": (True, rule.randomize),
        "room prefix": (spec.room_prefix, rule.room_prefix),
        "hide caller number": (spec.hide_caller_number, rule.hide_caller_number),
        "agent": ((spec.agent_name,), rule.agent_names),
    }
    return [field for field, (want, have) in checks.items() if want != have]


def reconcile(plan: TelephonyPlan, snapshot: SipSnapshot) -> Reconciliation:
    actions: list[str] = []
    problems: list[str] = []
    trunk_id = rule_id = ""

    ours = [
        trunk
        for trunk in snapshot.trunks
        if trunk.name == plan.trunk.name
        or any(number in trunk.numbers for number in plan.trunk.all_numbers)
    ]
    if len(ours) > 1:
        ids = ", ".join(trunk.id for trunk in ours)
        problems.append(f"Duplicate inbound trunks for this number or name: {ids}.")
    elif ours:
        trunk = ours[0]
        trunk_id = trunk.id
        if not trunk.authenticated:
            problems.append(
                f"Trunk {trunk.id} has no credentials and no address allow-list: "
                "it accepts calls from any address."
            )
        differences = _trunk_differences(plan, trunk)
        if differences:
            problems.append(
                f"Trunk {trunk.id} differs from the plan in: {', '.join(differences)}."
            )
        actions.append(KEEP_TRUNK)
    else:
        actions.append(CREATE_TRUNK)

    for trunk in snapshot.trunks:
        if trunk not in ours and not trunk.numbers and not trunk.authenticated:
            problems.append(
                f"Unrelated trunk {trunk.id} accepts any number from any address."
            )

    wildcards = [
        rule
        for rule in snapshot.rules
        if not rule.trunk_ids and rule.name != plan.dispatch.name
    ]
    for rule in wildcards:
        problems.append(
            f"Dispatch rule {rule.id} matches every trunk and would compete for "
            "our calls."
        )

    our_rules = [
        rule
        for rule in snapshot.rules
        if rule.name == plan.dispatch.name or (trunk_id and trunk_id in rule.trunk_ids)
    ]
    if len(our_rules) > 1:
        ids = ", ".join(rule.id for rule in our_rules)
        problems.append(f"Duplicate dispatch rules for this trunk or name: {ids}.")
    elif our_rules:
        rule = our_rules[0]
        rule_id = rule.id
        differences = _rule_differences(plan, rule, trunk_id)
        if differences:
            problems.append(
                f"Dispatch rule {rule.id} differs from the plan in: "
                f"{', '.join(differences)}."
            )
        actions.append(KEEP_DISPATCH)
    else:
        actions.append(CREATE_DISPATCH)

    return Reconciliation(
        actions=tuple(actions),
        problems=tuple(problems),
        trunk_id=trunk_id,
        rule_id=rule_id,
    )
