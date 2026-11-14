"""Turns settings into the inbound trunk and dispatch rule we intend to create.

Pure: no network, no LiveKit client. A plan with blockers must never be
provisioned, and the request builders refuse to build one.
"""

from dataclasses import asdict, dataclass

from google.protobuf.duration_pb2 import Duration
from livekit import api

from . import providers
from .conf import TelephonySettings
from .masking import mask_phone
from .providers import PROVIDERS

# Who has to act before a blocker clears.
CONFIG = "configuration"
OWNER = "owner"
LIVEKIT = "livekit"
PROVIDER = "provider"

_MEDIA_ENCRYPTION = {
    "require": api.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_REQUIRE,
    "allow": api.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_ALLOW,
}


class PlanBlockedError(RuntimeError):
    """Raised when something tries to build provisioning requests from a blocked plan."""


@dataclass(frozen=True)
class Blocker:
    code: str
    waiting_on: str
    message: str


@dataclass(frozen=True)
class TrunkSpec:
    name: str
    number: str
    auth_mode: str
    auth_username: str
    allowed_addresses: tuple[str, ...]
    media_encryption: str
    ringing_timeout_seconds: int
    max_call_duration_seconds: int


@dataclass(frozen=True)
class DispatchSpec:
    name: str
    room_prefix: str
    hide_caller_number: bool
    agent_name: str


@dataclass(frozen=True)
class TelephonyPlan:
    provider: str
    provider_label: str
    sip_endpoint: str
    provider_destination_uri: str
    trunk: TrunkSpec
    dispatch: DispatchSpec
    blockers: tuple[Blocker, ...]
    warnings: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.blockers

    def as_dict(self) -> dict:
        """Safe to print or log: the number is masked and there is no password."""
        data = asdict(self)
        data["trunk"]["number"] = mask_phone(self.trunk.number)
        data["ready"] = self.ready
        return data


def sip_endpoint(subdomain: str, region: str) -> str:
    if not subdomain:
        return ""
    if region == "global":
        return f"{subdomain}.sip.livekit.cloud"
    return f"{subdomain}.{region}.sip.livekit.cloud"


def build_plan(settings: TelephonySettings, *, agent_name: str) -> TelephonyPlan:
    blockers: list[Blocker] = []
    warnings: list[str] = []
    provider = PROVIDERS.get(settings.provider)

    def block(code: str, waiting_on: str, message: str) -> None:
        blockers.append(Blocker(code, waiting_on, message))

    def need(confirmation: str, waiting_on: str) -> None:
        if confirmation in settings.confirmed:
            return
        note = provider.note(confirmation) if provider else ""
        block(
            confirmation,
            waiting_on,
            f"Unconfirmed: {providers.CONFIRMATIONS[confirmation]} {note}".strip(),
        )

    if provider is None:
        block(
            "provider_not_chosen",
            CONFIG,
            f"Set TELEPHONY_PROVIDER to one of: {', '.join(sorted(PROVIDERS))}.",
        )
    if not settings.inbound_number:
        block(
            "inbound_number_missing",
            CONFIG,
            "Set TELEPHONY_INBOUND_NUMBER once a number exists. None has been bought.",
        )
    elif not settings.inbound_number.startswith("+91"):
        warnings.append("The inbound number is not an Indian (+91) number.")
    if not settings.sip_subdomain:
        block(
            "sip_subdomain_missing",
            CONFIG,
            "Set LIVEKIT_SIP_SUBDOMAIN to the LiveKit project ID without the p_ prefix.",
        )
    if not agent_name:
        block("agent_name_missing", CONFIG, "AGENT_NAME is empty.")

    if provider is not None:
        need(providers.ACCOUNT_READY, PROVIDER)
        required = provider.required_sip_region
        if required and settings.sip_region != required:
            block(
                "sip_region_mismatch",
                CONFIG,
                f"{provider.label} needs LIVEKIT_SIP_REGION={required}; "
                f"it is {settings.sip_region}.",
            )
    if settings.sip_region != "global":
        need(providers.REGIONAL_ENDPOINT, LIVEKIT)

    if settings.transport != "tls":
        block(
            "signalling_not_encrypted",
            CONFIG,
            "SIP_INBOUND_TRANSPORT must be tls: tcp sends call signalling, "
            "including the authentication exchange, unencrypted.",
        )

    # Matching the dialled number alone is never treated as authentication.
    if settings.auth_mode == "digest":
        if not (settings.auth_username and settings.auth_password):
            block(
                "trunk_credentials_missing",
                CONFIG,
                "Set SIP_INBOUND_AUTH_USERNAME and SIP_INBOUND_AUTH_PASSWORD. A trunk "
                "that only lists a number accepts calls to it from any address.",
            )
        need(providers.INBOUND_DIGEST_AUTH, PROVIDER)
    else:
        if not settings.allowed_addresses:
            block(
                "allow_list_empty",
                CONFIG,
                "Set SIP_INBOUND_ALLOWED_ADDRESSES to the provider's signalling "
                "addresses."
                + (
                    " Its documentation lists "
                    f"{', '.join(provider.documented_source_addresses)}; confirm "
                    "them with the provider before use."
                    if provider and provider.documented_source_addresses
                    else ""
                ),
            )
        need(providers.ALLOWED_ADDRESSES_ENFORCED, LIVEKIT)

    if settings.media_encryption == "off":
        block(
            "media_not_encrypted",
            CONFIG,
            "SIP_MEDIA_ENCRYPTION=off would carry caller audio unencrypted.",
        )
    elif settings.media_encryption == "require":
        need(providers.INBOUND_SRTP, PROVIDER)
    else:
        warnings.append(
            "SIP_MEDIA_ENCRYPTION=allow: audio is encrypted only if the provider "
            "offers SRTP, and falls back to unencrypted without notice."
        )

    if not settings.hide_caller_number:
        warnings.append(
            "SIP_HIDE_CALLER_NUMBER=false: the worker will receive caller numbers. "
            "Mask them in every log line."
        )
    if provider is not None:
        warnings.append(
            "Confirm the provider sends the dialled number as "
            f"{mask_phone(settings.inbound_number)} (E.164). A different format "
            "does not match the trunk, so calls are rejected rather than misrouted."
        )

    endpoint = sip_endpoint(settings.sip_subdomain, settings.sip_region)
    suffix = settings.provider or "unset"
    return TelephonyPlan(
        provider=settings.provider,
        provider_label=provider.label if provider else "(not chosen)",
        sip_endpoint=endpoint,
        provider_destination_uri=(
            f"sip:{endpoint};transport={settings.transport}" if endpoint else ""
        ),
        trunk=TrunkSpec(
            name=f"telugu-inbound-{suffix}",
            number=settings.inbound_number,
            auth_mode=settings.auth_mode,
            auth_username=(
                settings.auth_username if settings.auth_mode == "digest" else ""
            ),
            allowed_addresses=(
                settings.allowed_addresses
                if settings.auth_mode == "ip_allowlist"
                else ()
            ),
            media_encryption=settings.media_encryption,
            ringing_timeout_seconds=settings.ringing_timeout_seconds,
            max_call_duration_seconds=settings.max_call_duration_seconds,
        ),
        dispatch=DispatchSpec(
            name=f"telugu-inbound-{suffix}-dispatch",
            room_prefix=settings.room_prefix,
            hide_caller_number=settings.hide_caller_number,
            agent_name=agent_name,
        ),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def _require_ready(plan: TelephonyPlan) -> None:
    if not plan.ready:
        codes = ", ".join(blocker.code for blocker in plan.blockers)
        raise PlanBlockedError(f"Plan is blocked and must not be provisioned: {codes}")


def inbound_trunk_request(
    plan: TelephonyPlan, settings: TelephonySettings
) -> api.CreateSIPInboundTrunkRequest:
    """The request a future `apply` would send. Contains the trunk password."""
    _require_ready(plan)
    spec = plan.trunk
    trunk = api.SIPInboundTrunkInfo(
        name=spec.name,
        numbers=[spec.number],
        media_encryption=_MEDIA_ENCRYPTION[spec.media_encryption],
        ringing_timeout=Duration(seconds=spec.ringing_timeout_seconds),
        max_call_duration=Duration(seconds=spec.max_call_duration_seconds),
    )
    if spec.auth_mode == "digest":
        trunk.auth_username = spec.auth_username
        trunk.auth_password = settings.auth_password
    else:
        trunk.allowed_addresses.extend(spec.allowed_addresses)
    return api.CreateSIPInboundTrunkRequest(trunk=trunk)


def dispatch_rule_request(
    plan: TelephonyPlan, *, trunk_id: str
) -> api.CreateSIPDispatchRuleRequest:
    """The request a future `apply` would send once the trunk exists."""
    _require_ready(plan)
    if not trunk_id:
        # A rule without trunk_ids is a wildcard and would match every trunk.
        raise PlanBlockedError("A dispatch rule must be bound to its trunk ID.")
    spec = plan.dispatch
    return api.CreateSIPDispatchRuleRequest(
        name=spec.name,
        trunk_ids=[trunk_id],
        hide_phone_number=spec.hide_caller_number,
        # Callee rule with a random suffix: one room per call, named after our
        # own number. An individual rule would put the caller's number in the
        # room name, and so into logs.
        rule=api.SIPDispatchRule(
            dispatch_rule_callee=api.SIPDispatchRuleCallee(
                room_prefix=spec.room_prefix, randomize=True
            )
        ),
        room_config=api.RoomConfiguration(
            agents=[api.RoomAgentDispatch(agent_name=spec.agent_name)]
        ),
    )
