"""Minimal outbound calling: one stored trunk and one test call to one number.

Deliberately narrow. There is no list of numbers, no retry and no scheduling:
`place_test_call` dials the single number in OUTBOUND_TEST_ALLOWED_NUMBER and
nothing else. The voice worker does the dialling (see voice_agent/outbound.py);
Django only creates the trunk and asks the worker to place the call.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from uuid import uuid4

from livekit import api

from . import providers
from .conf import (
    _E164,
    _USERNAME,
    MIN_AUTH_PASSWORD_LENGTH,
    TelephonyConfigError,
    _bounded_int,
    _choice,
    _csv,
)
from .livekit_gateway import TelephonyGatewayError
from .masking import mask_phone, mask_phones_in
from .planning import CONFIG, PROVIDER, Blocker
from .provisioning import ProvisioningRefusedError

TRUNK_NAME = "telugu-outbound-vobiz"
ROOM_PREFIX = "outcall-"
# The worker reads the dial request from job metadata under this key.
METADATA_KEY = "outbound_call"

TRANSPORTS = {
    "tls": api.SIPTransport.SIP_TRANSPORT_TLS,
    "tcp": api.SIPTransport.SIP_TRANSPORT_TCP,
    "udp": api.SIPTransport.SIP_TRANSPORT_UDP,
}
MEDIA_ENCRYPTION = {
    "require": api.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_REQUIRE,
    "allow": api.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_ALLOW,
}


@dataclass(frozen=True)
class OutboundSettings:
    address: str
    auth_username: str
    auth_password: str = field(repr=False)
    caller_id: str = ""
    transport: str = "tls"
    media_encryption: str = "require"
    destination_country: str = "in"
    ringing_timeout_seconds: int = 30
    max_call_duration_seconds: int = 300
    allowed_number: str = ""
    confirmed: frozenset[str] = frozenset()


@dataclass(frozen=True)
class OutboundPlan:
    trunk_name: str
    address: str
    caller_id: str
    auth_username: str
    transport: str
    media_encryption: str
    destination_country: str
    allowed_number: str
    blockers: tuple[Blocker, ...]
    warnings: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.blockers

    def as_dict(self) -> dict:
        data = asdict(self)
        data["caller_id"] = mask_phone(self.caller_id)
        data["allowed_number"] = mask_phone(self.allowed_number)
        data["ready"] = self.ready
        return data


def _number(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip().replace(" ", "")
    if value and not _E164.match(value):
        raise TelephonyConfigError(
            f"{name} must be in E.164 form, such as +91XXXXXXXXXX."
        )
    return value


def load_outbound_settings(env: Mapping[str, str] | None = None) -> OutboundSettings:
    env = os.environ if env is None else env

    address = env.get("SIP_OUTBOUND_ADDRESS", "").strip().lower()
    if address and (
        address.startswith("sip:") or "/" in address or " " in address or "@" in address
    ):
        raise TelephonyConfigError(
            "SIP_OUTBOUND_ADDRESS is the provider's trunk domain only, without "
            "'sip:' or a user part."
        )
    username = env.get("SIP_OUTBOUND_AUTH_USERNAME", "").strip()
    if username and not _USERNAME.match(username):
        raise TelephonyConfigError(
            "SIP_OUTBOUND_AUTH_USERNAME must be 5 to 64 letters, digits, '.', '_' "
            "or '-'."
        )
    password = env.get("SIP_OUTBOUND_AUTH_PASSWORD", "")
    if password and len(password) < MIN_AUTH_PASSWORD_LENGTH:
        raise TelephonyConfigError(
            "SIP_OUTBOUND_AUTH_PASSWORD must be at least "
            f"{MIN_AUTH_PASSWORD_LENGTH} characters."
        )
    country = env.get("SIP_OUTBOUND_DESTINATION_COUNTRY", "").strip().lower() or "in"
    if not (len(country) == 2 and country.isalpha()):
        raise TelephonyConfigError(
            "SIP_OUTBOUND_DESTINATION_COUNTRY must be a two-letter country code."
        )
    confirmed = frozenset(item.lower() for item in _csv(env, "TELEPHONY_CONFIRMED"))
    unknown = sorted(confirmed - providers.CONFIRMATIONS.keys())
    if unknown:
        raise TelephonyConfigError(
            f"TELEPHONY_CONFIRMED has unknown entries: {', '.join(unknown)}."
        )
    return OutboundSettings(
        address=address,
        auth_username=username,
        auth_password=password,
        caller_id=_number(env, "SIP_OUTBOUND_CALLER_ID"),
        transport=_choice(env, "SIP_OUTBOUND_TRANSPORT", "tls", tuple(TRANSPORTS)),
        media_encryption=_choice(
            env, "SIP_OUTBOUND_MEDIA_ENCRYPTION", "require", tuple(MEDIA_ENCRYPTION)
        ),
        destination_country=country,
        ringing_timeout_seconds=_bounded_int(
            env, "SIP_OUTBOUND_RINGING_TIMEOUT_SECONDS", 30, 10, 60
        ),
        max_call_duration_seconds=_bounded_int(
            env, "SIP_OUTBOUND_MAX_CALL_DURATION_SECONDS", 300, 30, 600
        ),
        allowed_number=_number(env, "OUTBOUND_TEST_ALLOWED_NUMBER"),
        confirmed=confirmed,
    )


def build_outbound_plan(settings: OutboundSettings) -> OutboundPlan:
    blockers: list[Blocker] = []
    warnings: list[str] = []

    def block(code: str, waiting_on: str, message: str) -> None:
        blockers.append(Blocker(code, waiting_on, message))

    if not settings.address:
        block(
            "outbound_address_missing",
            CONFIG,
            "Set SIP_OUTBOUND_ADDRESS to the SIP domain of the Vobiz outbound trunk.",
        )
    if not (settings.auth_username and settings.auth_password):
        block(
            "outbound_credentials_missing",
            CONFIG,
            "Set SIP_OUTBOUND_AUTH_USERNAME and SIP_OUTBOUND_AUTH_PASSWORD to the "
            "credential attached to the Vobiz trunk.",
        )
    if not settings.caller_id:
        block(
            "caller_id_missing",
            CONFIG,
            "Set SIP_OUTBOUND_CALLER_ID to the Vobiz India number on your account.",
        )
    elif not settings.caller_id.startswith("+91"):
        block(
            "caller_id_not_indian",
            CONFIG,
            "Domestic Indian calls must present an Indian (+91) caller ID.",
        )
    if not settings.allowed_number:
        block(
            "allowed_number_missing",
            CONFIG,
            "Set OUTBOUND_TEST_ALLOWED_NUMBER to your own mobile. It is the only "
            "number that can be dialled.",
        )
    elif not settings.allowed_number.startswith("+91"):
        block(
            "allowed_number_not_indian",
            CONFIG,
            "The test number must be an Indian (+91) number: the trunk is pinned "
            "to India and media must stay in India.",
        )
    elif settings.allowed_number == settings.caller_id:
        block(
            "allowed_number_is_caller_id",
            CONFIG,
            "The test number and the caller ID are the same number.",
        )
    if settings.destination_country != "in":
        block(
            "not_pinned_to_india",
            CONFIG,
            "SIP_OUTBOUND_DESTINATION_COUNTRY must be 'in': Vobiz rejects domestic "
            "calls whose media leaves India.",
        )
    if providers.OUTBOUND_READY not in settings.confirmed:
        block(
            providers.OUTBOUND_READY,
            PROVIDER,
            f"Unconfirmed: {providers.CONFIRMATIONS[providers.OUTBOUND_READY]}",
        )
    if settings.transport != "tls":
        if providers.OUTBOUND_PLAIN_SIGNALLING not in settings.confirmed:
            block(
                providers.OUTBOUND_PLAIN_SIGNALLING,
                CONFIG,
                f"SIP_OUTBOUND_TRANSPORT={settings.transport} is unencrypted. "
                + providers.CONFIRMATIONS[providers.OUTBOUND_PLAIN_SIGNALLING],
            )
        else:
            warnings.append(
                f"Signalling uses {settings.transport}, unencrypted, as a recorded "
                "provider limitation. Dialled numbers are visible on the network."
            )
    if settings.media_encryption == "allow":
        warnings.append(
            "SIP_OUTBOUND_MEDIA_ENCRYPTION=allow: audio is encrypted only if the "
            "provider accepts SRTP."
        )

    return OutboundPlan(
        trunk_name=TRUNK_NAME,
        address=settings.address,
        caller_id=settings.caller_id,
        auth_username=settings.auth_username,
        transport=settings.transport,
        media_encryption=settings.media_encryption,
        destination_country=settings.destination_country,
        allowed_number=settings.allowed_number,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def outbound_trunk_request(
    plan: OutboundPlan, settings: OutboundSettings
) -> api.CreateSIPOutboundTrunkRequest:
    if not plan.ready:
        codes = ", ".join(blocker.code for blocker in plan.blockers)
        raise ProvisioningRefusedError(f"The outbound plan is blocked ({codes}).")
    return api.CreateSIPOutboundTrunkRequest(
        trunk=api.SIPOutboundTrunkInfo(
            name=plan.trunk_name,
            address=plan.address,
            # The only caller ID this trunk will present.
            numbers=[plan.caller_id],
            auth_username=plan.auth_username,
            auth_password=settings.auth_password,
            transport=TRANSPORTS[plan.transport],
            media_encryption=MEDIA_ENCRYPTION[plan.media_encryption],
            # Originate from LiveKit's India region so media stays in India.
            destination_country=plan.destination_country,
        )
    )


class OutboundGateway:
    """The LiveKit calls the outbound test needs, one client per call."""

    def __init__(
        self, *, url: str, api_key: str, api_secret: str, api_factory=api.LiveKitAPI
    ) -> None:
        self._args = (url, api_key, api_secret)
        self._api_factory = api_factory

    async def _call(self, service: str, method: str, request, secret: str = ""):
        client = self._api_factory(*self._args)
        try:
            return await getattr(getattr(client, service), method)(request)
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

    async def trunks(self) -> list:
        response = await self._call(
            "sip", "list_outbound_trunk", api.ListSIPOutboundTrunkRequest()
        )
        return list(response.items)

    async def create_trunk(self, request: api.CreateSIPOutboundTrunkRequest) -> str:
        created = await self._call(
            "sip", "create_outbound_trunk", request, secret=request.trunk.auth_password
        )
        return created.sip_trunk_id

    async def delete_trunk(self, trunk_id: str) -> None:
        await self._call(
            "sip", "delete_trunk", api.DeleteSIPTrunkRequest(sip_trunk_id=trunk_id)
        )

    async def active_test_rooms(self) -> list[str]:
        response = await self._call("room", "list_rooms", api.ListRoomsRequest())
        return [r.name for r in response.rooms if r.name.startswith(ROOM_PREFIX)]

    async def dispatch(self, request: api.CreateAgentDispatchRequest) -> None:
        await self._call("agent_dispatch", "create_dispatch", request)

    async def call_status(self, room: str) -> str | None:
        """`sip.callStatus` of the callee, "" before it exists, None once the room is gone."""
        try:
            response = await self._call(
                "room", "list_participants", api.ListParticipantsRequest(room=room)
            )
        except TelephonyGatewayError:
            return None
        for participant in response.participants:
            if participant.kind == api.ParticipantInfo.Kind.SIP:
                return participant.attributes.get("sip.callStatus", "")
        return ""


def _differences(plan: OutboundPlan, trunk) -> list[str]:
    checks = {
        "address": (plan.address, trunk.address),
        "caller ID": ([plan.caller_id], list(trunk.numbers)),
        "username": (plan.auth_username, trunk.auth_username),
        "transport": (TRANSPORTS[plan.transport], trunk.transport),
        "media encryption": (
            MEDIA_ENCRYPTION[plan.media_encryption],
            trunk.media_encryption,
        ),
        "destination country": (
            plan.destination_country,
            trunk.destination_country.lower(),
        ),
    }
    return [name for name, (want, have) in checks.items() if want != have]


async def find_trunk(plan: OutboundPlan, gateway: OutboundGateway) -> str:
    """ID of the one trunk matching the plan, or "" if none exists. Refuses on
    duplicates or drift: an existing trunk is never modified."""
    ours = [t for t in await gateway.trunks() if t.name == plan.trunk_name]
    if len(ours) > 1:
        ids = ", ".join(t.sip_trunk_id for t in ours)
        raise ProvisioningRefusedError(f"Duplicate outbound trunks: {ids}.")
    if not ours:
        return ""
    differences = _differences(plan, ours[0])
    if differences:
        raise ProvisioningRefusedError(
            f"Outbound trunk {ours[0].sip_trunk_id} differs from the plan in: "
            f"{', '.join(differences)}. Run outbound-teardown, then outbound-apply."
        )
    return ours[0].sip_trunk_id


async def apply_outbound(
    plan: OutboundPlan,
    settings: OutboundSettings,
    gateway: OutboundGateway,
    *,
    dry_run: bool,
) -> tuple[str, str]:
    """Returns (action, trunk_id); action is "kept", "would_create" or "created"."""
    request = outbound_trunk_request(plan, settings)
    trunk_id = await find_trunk(plan, gateway)
    if trunk_id:
        return "kept", trunk_id
    if dry_run:
        return "would_create", ""
    return "created", await gateway.create_trunk(request)


async def teardown_outbound(
    plan: OutboundPlan, gateway: OutboundGateway, *, dry_run: bool
) -> list[str]:
    ids = [t.sip_trunk_id for t in await gateway.trunks() if t.name == plan.trunk_name]
    if not dry_run:
        for trunk_id in ids:
            await gateway.delete_trunk(trunk_id)
    return ids


async def place_test_call(
    plan: OutboundPlan,
    settings: OutboundSettings,
    gateway: OutboundGateway,
    *,
    to: str,
    agent_name: str,
) -> str:
    """Ask the worker to place exactly one call. Returns the room name."""
    if not plan.ready:
        codes = ", ".join(blocker.code for blocker in plan.blockers)
        raise ProvisioningRefusedError(f"The outbound plan is blocked ({codes}).")
    if to != settings.allowed_number:
        raise ProvisioningRefusedError(
            "That number is not OUTBOUND_TEST_ALLOWED_NUMBER. Only the one "
            "configured test number can be called."
        )
    trunk_id = await find_trunk(plan, gateway)
    if not trunk_id:
        raise ProvisioningRefusedError(
            "No outbound trunk exists yet. Run `telephony outbound-apply` first."
        )
    active = await gateway.active_test_rooms()
    if active:
        raise ProvisioningRefusedError(
            f"A test call is already in progress ({', '.join(active)}). "
            "Wait for it to end."
        )
    room = f"{ROOM_PREFIX}{uuid4().hex}"
    metadata = {
        METADATA_KEY: {
            "to": to,
            "trunk_id": trunk_id,
            "ringing_timeout": settings.ringing_timeout_seconds,
            "max_call_duration": settings.max_call_duration_seconds,
        }
    }
    await gateway.dispatch(
        api.CreateAgentDispatchRequest(
            agent_name=agent_name, room=room, metadata=json.dumps(metadata)
        )
    )
    return room
