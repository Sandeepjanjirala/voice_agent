"""Offline telephony foundation: settings, plan, read-only status and refusals.

Nothing here touches LiveKit or a provider. The LiveKit client is replaced by a
fake that records every call and fails the test if anything other than a list
call is attempted.
"""

import io
import json
import re
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from google.protobuf.duration_pb2 import Duration
from livekit import api

from apps.telephony import livekit_gateway, providers, reconcile
from apps.telephony.conf import TelephonyConfigError, load_telephony_settings
from apps.telephony.masking import mask_phone, mask_phones_in
from apps.telephony.planning import (
    PlanBlockedError,
    build_plan,
    dispatch_rule_request,
    inbound_trunk_request,
)

ROOT = Path(__file__).resolve().parents[2]

NUMBER = "+918045550123"
PASSWORD = "s3cret-Trunk-pass!"
ALL_CONFIRMED = (
    f"{providers.ACCOUNT_READY},{providers.REGIONAL_ENDPOINT},"
    f"{providers.INBOUND_DIGEST_AUTH},{providers.INBOUND_SRTP}"
)
READY_ENV = {
    "TELEPHONY_PROVIDER": "plivo",
    "TELEPHONY_INBOUND_NUMBER": NUMBER,
    "LIVEKIT_SIP_SUBDOMAIN": "abc123def45",
    "SIP_INBOUND_AUTH_USERNAME": "lkinbound",
    "SIP_INBOUND_AUTH_PASSWORD": PASSWORD,
    "TELEPHONY_CONFIRMED": ALL_CONFIRMED,
}
TELEPHONY_ENV_NAMES = (
    "TELEPHONY_PROVIDER",
    "TELEPHONY_INBOUND_NUMBER",
    "TELEPHONY_CONFIRMED",
    "LIVEKIT_SIP_SUBDOMAIN",
    "LIVEKIT_SIP_REGION",
    "SIP_INBOUND_TRANSPORT",
    "SIP_INBOUND_AUTH_MODE",
    "SIP_INBOUND_AUTH_USERNAME",
    "SIP_INBOUND_AUTH_PASSWORD",
    "SIP_INBOUND_ALLOWED_ADDRESSES",
    "SIP_MEDIA_ENCRYPTION",
    "SIP_RINGING_TIMEOUT_SECONDS",
    "SIP_MAX_CALL_DURATION_SECONDS",
    "SIP_HIDE_CALLER_NUMBER",
    "SIP_ROOM_PREFIX",
)


def plan_for(**overrides):
    settings = load_telephony_settings({**READY_ENV, **overrides})
    return build_plan(settings, agent_name="telugu-agent"), settings


def blocker_codes(plan) -> set[str]:
    return {blocker.code for blocker in plan.blockers}


# --- masking ---------------------------------------------------------------


def test_phone_numbers_are_masked() -> None:
    assert mask_phone("+919876543210") == "+91********10"
    assert mask_phone("09876543210") == "09*******10"
    assert mask_phone("") == "(none)"
    assert "9876543" not in mask_phones_in("call from +91 98765 43210 failed")


# --- settings --------------------------------------------------------------


def test_defaults_are_the_secure_choices() -> None:
    settings = load_telephony_settings({})

    assert settings.provider == ""
    assert settings.transport == "tls"
    assert settings.auth_mode == "digest"
    assert settings.media_encryption == "require"
    assert settings.hide_caller_number is True
    assert settings.confirmed == frozenset()


def test_indian_providers_default_to_the_india_sip_region() -> None:
    assert (
        load_telephony_settings({"TELEPHONY_PROVIDER": "plivo"}).sip_region == "india"
    )
    assert (
        load_telephony_settings({"TELEPHONY_PROVIDER": "vobiz"}).sip_region == "india"
    )
    assert load_telephony_settings({}).sip_region == "global"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TELEPHONY_PROVIDER", "twilio"),
        ("TELEPHONY_INBOUND_NUMBER", "08045550123"),
        ("TELEPHONY_INBOUND_NUMBER", "+91-not-a-number"),
        ("LIVEKIT_SIP_SUBDOMAIN", "p_abc123def45"),
        ("LIVEKIT_SIP_SUBDOMAIN", "abc.sip.livekit.cloud"),
        ("LIVEKIT_SIP_REGION", "mumbai"),
        ("SIP_INBOUND_TRANSPORT", "udp"),
        ("SIP_INBOUND_AUTH_MODE", "none"),
        ("SIP_INBOUND_AUTH_PASSWORD", "short"),
        ("SIP_INBOUND_ALLOWED_ADDRESSES", "0.0.0.0/0"),
        ("SIP_INBOUND_ALLOWED_ADDRESSES", "sip.example.com"),
        ("SIP_MEDIA_ENCRYPTION", "maybe"),
        ("SIP_RINGING_TIMEOUT_SECONDS", "600"),
        ("SIP_MAX_CALL_DURATION_SECONDS", "ten"),
        ("SIP_HIDE_CALLER_NUMBER", "sometimes"),
        ("SIP_ROOM_PREFIX", "Call Room"),
        ("TELEPHONY_CONFIRMED", "everything_is_fine"),
    ],
)
def test_malformed_values_are_rejected(name: str, value: str) -> None:
    with pytest.raises(TelephonyConfigError, match=name):
        load_telephony_settings({name: value})


def test_config_errors_do_not_echo_the_password() -> None:
    with pytest.raises(TelephonyConfigError) as raised:
        load_telephony_settings({"SIP_INBOUND_AUTH_PASSWORD": "tiny-pw"})

    assert "tiny-pw" not in str(raised.value)


def test_password_never_appears_in_repr_or_plan_output() -> None:
    plan, settings = plan_for()

    assert PASSWORD not in repr(settings)
    assert PASSWORD not in repr(plan)
    assert PASSWORD not in json.dumps(plan.as_dict())
    assert NUMBER not in json.dumps(plan.as_dict())


def test_every_telephony_variable_is_documented_in_env_example() -> None:
    source = (ROOT / "apps" / "telephony" / "conf.py").read_text(encoding="utf-8")
    documented = (ROOT / ".env.example").read_text(encoding="utf-8")

    read_by_code = set(re.findall(r'"((?:TELEPHONY|LIVEKIT_SIP|SIP)_[A-Z_]+)"', source))

    assert read_by_code == set(TELEPHONY_ENV_NAMES)
    assert [n for n in sorted(read_by_code) if f"\n{n}=" not in documented] == []


# --- plan: blockers --------------------------------------------------------


def test_unconfigured_plan_is_blocked_and_explains_why() -> None:
    plan = build_plan(load_telephony_settings({}), agent_name="telugu-agent")

    assert not plan.ready
    assert {
        "provider_not_chosen",
        "inbound_number_missing",
        "sip_subdomain_missing",
        "trunk_credentials_missing",
    } <= blocker_codes(plan)


def test_fully_confirmed_plan_is_ready() -> None:
    plan, _ = plan_for()

    assert plan.ready
    assert plan.sip_endpoint == "abc123def45.india.sip.livekit.cloud"
    assert plan.provider_destination_uri == (
        "sip:abc123def45.india.sip.livekit.cloud;transport=tls"
    )
    assert plan.trunk.name == "telugu-inbound-plivo"
    assert plan.dispatch.agent_name == "telugu-agent"


@pytest.mark.parametrize("provider", sorted(providers.PROVIDERS))
@pytest.mark.parametrize(
    "confirmation",
    [
        providers.ACCOUNT_READY,
        providers.REGIONAL_ENDPOINT,
        providers.INBOUND_DIGEST_AUTH,
        providers.INBOUND_SRTP,
    ],
)
def test_each_unverified_capability_blocks_until_confirmed(
    provider: str, confirmation: str
) -> None:
    remaining = ",".join(c for c in ALL_CONFIRMED.split(",") if c != confirmation)

    plan, _ = plan_for(TELEPHONY_PROVIDER=provider, TELEPHONY_CONFIRMED=remaining)

    assert blocker_codes(plan) == {confirmation}


def test_number_matching_alone_is_never_accepted_as_authentication() -> None:
    plan, _ = plan_for(SIP_INBOUND_AUTH_USERNAME="", SIP_INBOUND_AUTH_PASSWORD="")

    assert "trunk_credentials_missing" in blocker_codes(plan)


def test_allow_list_needs_addresses_and_livekit_enforcement() -> None:
    plan, _ = plan_for(SIP_INBOUND_AUTH_MODE="ip_allowlist")
    assert {
        "allow_list_empty",
        providers.ALLOWED_ADDRESSES_ENFORCED,
    } <= blocker_codes(plan)

    plan, _ = plan_for(
        SIP_INBOUND_AUTH_MODE="ip_allowlist",
        SIP_INBOUND_ALLOWED_ADDRESSES="203.0.113.10, 198.51.100.0/24",
    )
    # Saving the list is not enough: LiveKit ignores it until support enables it.
    assert blocker_codes(plan) == {providers.ALLOWED_ADDRESSES_ENFORCED}

    plan, _ = plan_for(
        SIP_INBOUND_AUTH_MODE="ip_allowlist",
        SIP_INBOUND_ALLOWED_ADDRESSES="203.0.113.10",
        TELEPHONY_CONFIRMED=f"{ALL_CONFIRMED},{providers.ALLOWED_ADDRESSES_ENFORCED}",
    )
    assert plan.ready
    assert plan.trunk.auth_username == ""


def test_unencrypted_signalling_or_media_is_blocked() -> None:
    assert "signalling_not_encrypted" in blocker_codes(
        plan_for(SIP_INBOUND_TRANSPORT="tcp")[0]
    )
    assert "media_not_encrypted" in blocker_codes(
        plan_for(SIP_MEDIA_ENCRYPTION="off")[0]
    )


def test_optional_media_encryption_is_allowed_with_a_warning() -> None:
    confirmed = ALL_CONFIRMED.replace(f",{providers.INBOUND_SRTP}", "")

    plan, _ = plan_for(SIP_MEDIA_ENCRYPTION="allow", TELEPHONY_CONFIRMED=confirmed)

    assert plan.ready
    assert any("falls back to unencrypted" in w for w in plan.warnings)


def test_global_endpoint_is_blocked_for_indian_providers() -> None:
    plan, _ = plan_for(LIVEKIT_SIP_REGION="global")

    assert "sip_region_mismatch" in blocker_codes(plan)
    assert plan.sip_endpoint == "abc123def45.sip.livekit.cloud"


# --- plan: requests a future apply would send -------------------------------


def test_trunk_request_is_authenticated_encrypted_and_time_limited() -> None:
    plan, settings = plan_for()

    trunk = inbound_trunk_request(plan, settings).trunk

    assert list(trunk.numbers) == [NUMBER]
    assert trunk.auth_username == "lkinbound"
    assert trunk.auth_password == PASSWORD
    assert trunk.media_encryption == api.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_REQUIRE
    assert trunk.ringing_timeout.seconds == 25
    assert trunk.max_call_duration.seconds == 600
    assert list(trunk.allowed_addresses) == []


def test_dispatch_request_isolates_rooms_and_reuses_the_existing_agent() -> None:
    plan, _ = plan_for()

    request = dispatch_rule_request(plan, trunk_id="ST_abc")

    assert list(request.trunk_ids) == ["ST_abc"]
    assert request.rule.WhichOneof("rule") == "dispatch_rule_callee"
    assert request.rule.dispatch_rule_callee.randomize is True
    assert request.rule.dispatch_rule_callee.room_prefix == "call-"
    assert request.hide_phone_number is True
    assert [a.agent_name for a in request.room_config.agents] == ["telugu-agent"]


def test_requests_cannot_be_built_from_a_blocked_plan() -> None:
    plan, settings = plan_for(TELEPHONY_CONFIRMED="")

    with pytest.raises(PlanBlockedError):
        inbound_trunk_request(plan, settings)
    with pytest.raises(PlanBlockedError):
        dispatch_rule_request(plan, trunk_id="ST_abc")


def test_dispatch_rule_is_never_a_wildcard() -> None:
    plan, _ = plan_for()

    with pytest.raises(PlanBlockedError, match="bound to its trunk"):
        dispatch_rule_request(plan, trunk_id="")


# --- read-only gateway ------------------------------------------------------


class _FakeSip:
    """Stands in for LiveKitAPI.sip. Anything except a list call fails the test."""

    def __init__(self, owner) -> None:
        self._owner = owner

    async def list_inbound_trunk(self, request):
        self._owner.calls.append("list_inbound_trunk")
        if self._owner.fail:
            raise ConnectionError(f"boom for {NUMBER} with {PASSWORD}")
        return api.ListSIPInboundTrunkResponse(items=self._owner.trunks)

    async def list_dispatch_rule(self, request):
        self._owner.calls.append("list_dispatch_rule")
        return api.ListSIPDispatchRuleResponse(items=self._owner.rules)

    def __getattr__(self, name):
        raise AssertionError(f"mutating or unexpected SIP call attempted: {name}")


class FakeLiveKit:
    def __init__(self, trunks=(), rules=(), *, fail=False) -> None:
        self.trunks, self.rules, self.fail = list(trunks), list(rules), fail
        self.calls: list[str] = []
        self.closed = False
        self.sip = _FakeSip(self)

    def factory(self, url, api_key, api_secret):
        return self

    async def aclose(self) -> None:
        self.closed = True

    def __getattr__(self, name):
        raise AssertionError(f"unexpected LiveKit service used: {name}")


def existing_trunk(**overrides) -> api.SIPInboundTrunkInfo:
    fields = {
        "sip_trunk_id": "ST_abc",
        "name": "telugu-inbound-plivo",
        "numbers": [NUMBER],
        "auth_username": "lkinbound",
        "auth_password": PASSWORD,
        "media_encryption": api.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_REQUIRE,
        "ringing_timeout": Duration(seconds=25),
        "max_call_duration": Duration(seconds=600),
    }
    return api.SIPInboundTrunkInfo(**{**fields, **overrides})


def existing_rule(**overrides) -> api.SIPDispatchRuleInfo:
    fields = {
        "sip_dispatch_rule_id": "SDR_abc",
        "name": "telugu-inbound-plivo-dispatch",
        "trunk_ids": ["ST_abc"],
        "hide_phone_number": True,
        "rule": api.SIPDispatchRule(
            dispatch_rule_callee=api.SIPDispatchRuleCallee(
                room_prefix="call-", randomize=True
            )
        ),
        "room_config": api.RoomConfiguration(
            agents=[api.RoomAgentDispatch(agent_name="telugu-agent")]
        ),
    }
    return api.SIPDispatchRuleInfo(**{**fields, **overrides})


async def snapshot_of(fake: FakeLiveKit):
    gateway = livekit_gateway.ReadOnlySipGateway(
        url="wss://test.livekit.cloud",
        api_key="key",
        api_secret="secret",
        api_factory=fake.factory,
    )
    return await gateway.snapshot()


async def test_snapshot_only_lists_and_always_closes_the_client() -> None:
    fake = FakeLiveKit([existing_trunk()], [existing_rule()])

    snapshot = await snapshot_of(fake)

    assert fake.calls == ["list_inbound_trunk", "list_dispatch_rule"]
    assert fake.closed
    assert snapshot.trunks[0].id == "ST_abc"
    assert snapshot.rules[0].kind == "callee"
    assert snapshot.rules[0].agent_names == ("telugu-agent",)


async def test_snapshot_drops_the_trunk_password() -> None:
    snapshot = await snapshot_of(FakeLiveKit([existing_trunk()]))

    assert PASSWORD not in repr(snapshot)


async def test_gateway_errors_are_sanitised_and_the_client_is_closed() -> None:
    fake = FakeLiveKit(fail=True)

    with pytest.raises(livekit_gateway.TelephonyGatewayError) as raised:
        await snapshot_of(fake)

    assert fake.closed
    assert "ConnectionError" in str(raised.value)
    assert PASSWORD not in str(raised.value)
    assert NUMBER not in str(raised.value)
    assert raised.value.__cause__ is None


def test_gateway_has_no_way_to_change_livekit() -> None:
    source = "".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "apps" / "telephony").rglob("*.py")
    )

    # Request builders exist for a future apply; nothing may send them yet.
    assert re.findall(r"\.sip\.(\w+)\(", source) == [
        "list_inbound_trunk",
        "list_dispatch_rule",
    ]
    for forbidden in ("create_", "update_", "delete_", "transfer_", "requests."):
        assert f".{forbidden}" not in source.replace("api.Create", "")


# --- reconcile: routing, duplicates, drift ----------------------------------


async def reconciled(trunks=(), rules=(), **env):
    plan, _ = plan_for(**env)
    return reconcile.reconcile(plan, await snapshot_of(FakeLiveKit(trunks, rules)))


async def test_empty_project_needs_both_resources() -> None:
    result = await reconciled()

    assert result.safe_to_apply
    assert result.actions == (reconcile.CREATE_TRUNK, reconcile.CREATE_DISPATCH)


async def test_repeating_apply_would_create_nothing() -> None:
    result = await reconciled([existing_trunk()], [existing_rule()])

    assert result.nothing_to_do
    assert (result.trunk_id, result.rule_id) == ("ST_abc", "SDR_abc")


async def test_missing_dispatch_rule_is_the_only_thing_created() -> None:
    result = await reconciled([existing_trunk()])

    assert result.safe_to_apply
    assert result.actions == (reconcile.KEEP_TRUNK, reconcile.CREATE_DISPATCH)


async def test_duplicate_trunks_stop_for_a_person() -> None:
    result = await reconciled(
        [existing_trunk(), existing_trunk(sip_trunk_id="ST_second", name="copy")]
    )

    assert not result.safe_to_apply
    assert "Duplicate inbound trunks" in result.problems[0]


async def test_duplicate_dispatch_rules_stop_for_a_person() -> None:
    result = await reconciled(
        [existing_trunk()],
        [existing_rule(), existing_rule(sip_dispatch_rule_id="SDR_two", name="copy")],
    )

    assert any("Duplicate dispatch rules" in p for p in result.problems)


async def test_existing_unauthenticated_trunk_is_reported() -> None:
    result = await reconciled([existing_trunk(auth_username="", auth_password="")])

    assert any("accepts calls from any address" in p for p in result.problems)


async def test_drift_is_reported_field_by_field_and_not_overwritten() -> None:
    result = await reconciled(
        [
            existing_trunk(
                media_encryption=api.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_DISABLE
            )
        ],
        [
            existing_rule(
                room_config=api.RoomConfiguration(
                    agents=[api.RoomAgentDispatch(agent_name="other-agent")]
                )
            )
        ],
    )

    assert not result.safe_to_apply
    assert any("media encryption" in p for p in result.problems)
    assert any("agent" in p for p in result.problems)


async def test_caller_number_room_names_count_as_drift() -> None:
    individual = api.SIPDispatchRule(
        dispatch_rule_individual=api.SIPDispatchRuleIndividual(room_prefix="call-")
    )

    result = await reconciled([existing_trunk()], [existing_rule(rule=individual)])

    assert any("rule type" in p for p in result.problems)


async def test_wildcard_rules_and_open_trunks_are_flagged() -> None:
    result = await reconciled(
        [api.SIPInboundTrunkInfo(sip_trunk_id="ST_open", name="open")],
        [
            existing_rule(
                sip_dispatch_rule_id="SDR_wild", name="catch-all", trunk_ids=[]
            )
        ],
    )

    assert any("matches every trunk" in p for p in result.problems)
    assert any("accepts any number from any address" in p for p in result.problems)


# --- management command -----------------------------------------------------


@pytest.fixture
def env(monkeypatch, settings):
    """Pin the telephony environment so a developer's .env.local cannot leak in."""
    for name in TELEPHONY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    settings.LIVEKIT_URL = "wss://test.livekit.cloud"
    settings.LIVEKIT_API_KEY = "test-api-key"
    settings.LIVEKIT_API_SECRET = "test-secret-with-at-least-32-characters"
    settings.LIVEKIT_AGENT_NAME = "telugu-agent"

    def set_env(**values):
        for name, value in values.items():
            monkeypatch.setenv(name, value)

    return set_env


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("this command must not contact LiveKit")

    monkeypatch.setattr(livekit_gateway.api, "LiveKitAPI", refuse)
    monkeypatch.setattr(
        "apps.telephony.management.commands.telephony.ReadOnlySipGateway", refuse
    )


@pytest.fixture
def fake_livekit(monkeypatch):
    def install(fake: FakeLiveKit) -> FakeLiveKit:
        real = livekit_gateway.ReadOnlySipGateway

        def build(**kwargs):
            return real(**kwargs, api_factory=fake.factory)

        monkeypatch.setattr(
            "apps.telephony.management.commands.telephony.ReadOnlySipGateway", build
        )
        return fake

    return install


def run(*args: str) -> str:
    out = io.StringIO()
    call_command("telephony", *args, stdout=out)
    return out.getvalue()


def test_plan_runs_offline_when_nothing_is_configured(env, no_network) -> None:
    output = run("plan")

    assert "NOT READY" in output
    assert "provider_not_chosen" in output


def test_plan_masks_the_number_and_hides_the_password(env, no_network) -> None:
    env(**READY_ENV)

    text, as_json = run("plan"), run("plan", "--json")

    for output in (text, as_json):
        assert NUMBER not in output
        assert PASSWORD not in output
    assert "+91********23" in text
    assert "READY: no blockers" in text
    assert json.loads(as_json)["ready"] is True


def test_bad_configuration_is_a_clean_command_error(env, no_network) -> None:
    env(TELEPHONY_INBOUND_NUMBER="12345")

    with pytest.raises(CommandError, match="TELEPHONY_INBOUND_NUMBER"):
        run("plan")


def test_status_reads_only_and_reports_an_empty_project(env, fake_livekit) -> None:
    env(**READY_ENV)
    fake = fake_livekit(FakeLiveKit())

    output = run("status")

    assert fake.calls == ["list_inbound_trunk", "list_dispatch_rule"]
    assert "Inbound trunks: 0" in output
    assert "create_trunk, create_dispatch_rule" in output


def test_status_recognises_a_matching_setup_without_leaking(env, fake_livekit) -> None:
    env(**READY_ENV)
    fake_livekit(FakeLiveKit([existing_trunk()], [existing_rule()]))

    output = run("status")

    assert "already exist and match" in output
    assert NUMBER not in output
    assert PASSWORD not in output


def test_status_reports_network_failure_without_a_traceback(env, fake_livekit) -> None:
    env(**READY_ENV)
    fake_livekit(FakeLiveKit(fail=True))

    with pytest.raises(CommandError, match="Could not read SIP configuration"):
        run("status")


def test_status_needs_livekit_credentials(env, settings, no_network) -> None:
    settings.LIVEKIT_API_SECRET = ""

    with pytest.raises(CommandError, match="LIVEKIT_API_SECRET"):
        run("status")


def test_apply_refuses_a_blocked_plan(env, no_network) -> None:
    env(**{**READY_ENV, "TELEPHONY_CONFIRMED": ""})

    with pytest.raises(CommandError, match="plan is blocked"):
        run("apply", "--confirm-number", NUMBER, "--yes-change-livekit")


def test_apply_needs_the_typed_number_and_the_explicit_flag(env, no_network) -> None:
    env(**READY_ENV)

    with pytest.raises(CommandError, match="--confirm-number"):
        run("apply", "--yes-change-livekit")
    with pytest.raises(CommandError, match="--confirm-number"):
        run("apply", "--confirm-number", "+918045550999", "--yes-change-livekit")
    with pytest.raises(CommandError, match="--yes-change-livekit"):
        run("apply", "--confirm-number", NUMBER)


def test_apply_is_disabled_even_when_fully_confirmed(env, no_network) -> None:
    env(**READY_ENV)

    with pytest.raises(CommandError, match="not included in this build"):
        run("apply", "--confirm-number", NUMBER, "--yes-change-livekit")


def test_teardown_needs_confirmation_and_is_disabled(env, no_network) -> None:
    env(**READY_ENV)

    with pytest.raises(CommandError, match="--confirm-trunk-id"):
        run("teardown", "--yes-delete-from-livekit")
    with pytest.raises(CommandError, match="--yes-delete-from-livekit"):
        run("teardown", "--confirm-trunk-id", "ST_abc")
    with pytest.raises(CommandError, match="not included in this build"):
        run("teardown", "--confirm-trunk-id", "ST_abc", "--yes-delete-from-livekit")
