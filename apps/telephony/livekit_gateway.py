"""Read-only view of the SIP configuration held by LiveKit Cloud.

This module only lists. The calls that change LiveKit live in provisioning.py.
"""

from dataclasses import dataclass

from livekit import api


class TelephonyGatewayError(RuntimeError):
    """LiveKit could not be reached or refused the read."""


@dataclass(frozen=True)
class TrunkView:
    id: str
    name: str
    numbers: tuple[str, ...]
    auth_username: str
    allowed_addresses: tuple[str, ...]
    media_encryption: str
    ringing_timeout_seconds: int
    max_call_duration_seconds: int

    @property
    def authenticated(self) -> bool:
        return bool(self.auth_username or self.allowed_addresses)


@dataclass(frozen=True)
class RuleView:
    id: str
    name: str
    trunk_ids: tuple[str, ...]
    kind: str
    room_prefix: str
    randomize: bool
    hide_caller_number: bool
    agent_names: tuple[str, ...]


@dataclass(frozen=True)
class SipSnapshot:
    trunks: tuple[TrunkView, ...]
    rules: tuple[RuleView, ...]


_ENCRYPTION_NAMES = {
    api.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_DISABLE: "off",
    api.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_ALLOW: "allow",
    api.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_REQUIRE: "require",
}


def _trunk_view(trunk) -> TrunkView:
    # auth_password is dropped here so it cannot reach output or logs.
    return TrunkView(
        id=trunk.sip_trunk_id,
        name=trunk.name,
        numbers=tuple(trunk.numbers),
        auth_username=trunk.auth_username,
        allowed_addresses=tuple(trunk.allowed_addresses),
        media_encryption=_ENCRYPTION_NAMES.get(trunk.media_encryption, "unknown"),
        ringing_timeout_seconds=trunk.ringing_timeout.seconds,
        max_call_duration_seconds=trunk.max_call_duration.seconds,
    )


def _rule_view(rule) -> RuleView:
    kind = rule.rule.WhichOneof("rule") or "unknown"
    detail = getattr(rule.rule, kind, None)
    return RuleView(
        id=rule.sip_dispatch_rule_id,
        name=rule.name,
        trunk_ids=tuple(rule.trunk_ids),
        kind=kind.removeprefix("dispatch_rule_"),
        room_prefix=getattr(detail, "room_prefix", ""),
        randomize=bool(getattr(detail, "randomize", False)),
        hide_caller_number=rule.hide_phone_number,
        agent_names=tuple(agent.agent_name for agent in rule.room_config.agents),
    )


class ReadOnlySipGateway:
    def __init__(
        self, *, url: str, api_key: str, api_secret: str, api_factory=api.LiveKitAPI
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_factory = api_factory

    async def snapshot(self) -> SipSnapshot:
        client = self._api_factory(self._url, self._api_key, self._api_secret)
        try:
            trunks = await client.sip.list_inbound_trunk(
                api.ListSIPInboundTrunkRequest()
            )
            rules = await client.sip.list_dispatch_rule(
                api.ListSIPDispatchRuleRequest()
            )
        except Exception as error:
            # The original message may carry request details; keep only the type.
            raise TelephonyGatewayError(
                f"Could not read SIP configuration from LiveKit "
                f"({type(error).__name__})."
            ) from None
        finally:
            await client.aclose()
        return SipSnapshot(
            trunks=tuple(_trunk_view(trunk) for trunk in trunks.items),
            rules=tuple(_rule_view(rule) for rule in rules.items),
        )
