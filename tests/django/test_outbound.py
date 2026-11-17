"""Outbound test call: settings, plan, trunk provisioning and the one-call command.

No test here contacts LiveKit or places a call. The LiveKit client is a fake
that records every request.
"""

import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from livekit import api

from apps.telephony import outbound, providers
from apps.telephony.conf import TelephonyConfigError

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.django_db
COMMAND = "apps.telephony.management.commands.telephony"

MOBILE = "+919876543210"
CALLER_ID = "+918045550123"
PASSWORD = "s3cret-Outbound-pw!"
READY_ENV = {
    "SIP_OUTBOUND_ADDRESS": "abc123.sip.vobiz.ai",
    "SIP_OUTBOUND_AUTH_USERNAME": "lkoutbound",
    "SIP_OUTBOUND_AUTH_PASSWORD": PASSWORD,
    "SIP_OUTBOUND_CALLER_ID": CALLER_ID,
    "OUTBOUND_TEST_ALLOWED_NUMBER": MOBILE,
    "TELEPHONY_CONFIRMED": providers.OUTBOUND_READY,
}
ENV_NAMES = (
    "SIP_OUTBOUND_ADDRESS",
    "SIP_OUTBOUND_AUTH_USERNAME",
    "SIP_OUTBOUND_AUTH_PASSWORD",
    "SIP_OUTBOUND_CALLER_ID",
    "SIP_OUTBOUND_TRANSPORT",
    "SIP_OUTBOUND_MEDIA_ENCRYPTION",
    "SIP_OUTBOUND_DESTINATION_COUNTRY",
    "SIP_OUTBOUND_RINGING_TIMEOUT_SECONDS",
    "SIP_OUTBOUND_MAX_CALL_DURATION_SECONDS",
    "OUTBOUND_TEST_ALLOWED_NUMBER",
    "TELEPHONY_CONFIRMED",
)


def plan_for(**overrides):
    settings = outbound.load_outbound_settings({**READY_ENV, **overrides})
    return outbound.build_outbound_plan(settings), settings


def codes(plan) -> set[str]:
    return {blocker.code for blocker in plan.blockers}


# --- settings and plan ------------------------------------------------------


def test_defaults_are_secure_and_pinned_to_india() -> None:
    settings = outbound.load_outbound_settings({})

    assert settings.transport == "tls"
    assert settings.media_encryption == "require"
    assert settings.destination_country == "in"
    assert settings.allowed_number == ""


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SIP_OUTBOUND_ADDRESS", "sip:abc.sip.vobiz.ai"),
        ("SIP_OUTBOUND_ADDRESS", "user@abc.sip.vobiz.ai"),
        ("SIP_OUTBOUND_AUTH_PASSWORD", "short"),
        ("SIP_OUTBOUND_CALLER_ID", "08045550123"),
        ("OUTBOUND_TEST_ALLOWED_NUMBER", "9876543210"),
        ("OUTBOUND_TEST_ALLOWED_NUMBER", "+919876543210,+919876543211"),
        ("SIP_OUTBOUND_TRANSPORT", "ws"),
        ("SIP_OUTBOUND_MEDIA_ENCRYPTION", "off"),
        ("SIP_OUTBOUND_DESTINATION_COUNTRY", "india"),
        ("SIP_OUTBOUND_RINGING_TIMEOUT_SECONDS", "600"),
        ("SIP_OUTBOUND_MAX_CALL_DURATION_SECONDS", "7200"),
    ],
)
def test_malformed_values_are_rejected(name: str, value: str) -> None:
    with pytest.raises(TelephonyConfigError, match=name):
        outbound.load_outbound_settings({name: value})


def test_every_outbound_variable_is_documented() -> None:
    source = (ROOT / "apps" / "telephony" / "outbound.py").read_text(encoding="utf-8")
    documented = (ROOT / ".env.example").read_text(encoding="utf-8")

    names = set(
        re.findall(r'"((?:SIP_OUTBOUND|OUTBOUND_TEST|TELEPHONY)_[A-Z_]+)"', source)
    )

    assert names == set(ENV_NAMES)
    assert [n for n in sorted(names) if f"\n{n}=" not in documented] == []


def test_unconfigured_plan_lists_what_is_missing() -> None:
    plan = outbound.build_outbound_plan(outbound.load_outbound_settings({}))

    assert codes(plan) == {
        "outbound_address_missing",
        "outbound_credentials_missing",
        "caller_id_missing",
        "allowed_number_missing",
        providers.OUTBOUND_READY,
    }


def test_configured_and_confirmed_plan_is_ready() -> None:
    plan, _ = plan_for()

    assert plan.ready
    assert plan.warnings == ()


def test_account_permission_is_never_assumed() -> None:
    plan, _ = plan_for(TELEPHONY_CONFIRMED="")

    assert codes(plan) == {providers.OUTBOUND_READY}


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"SIP_OUTBOUND_CALLER_ID": "+15105550100"}, "caller_id_not_indian"),
        ({"OUTBOUND_TEST_ALLOWED_NUMBER": "+15105550100"}, "allowed_number_not_indian"),
        ({"OUTBOUND_TEST_ALLOWED_NUMBER": CALLER_ID}, "allowed_number_is_caller_id"),
        ({"SIP_OUTBOUND_DESTINATION_COUNTRY": "us"}, "not_pinned_to_india"),
        ({"SIP_OUTBOUND_TRANSPORT": "tcp"}, providers.OUTBOUND_PLAIN_SIGNALLING),
        ({"SIP_OUTBOUND_TRANSPORT": "udp"}, providers.OUTBOUND_PLAIN_SIGNALLING),
    ],
)
def test_unsafe_or_unsupported_choices_block(overrides, code) -> None:
    plan, _ = plan_for(**overrides)

    assert codes(plan) == {code}


def test_plain_signalling_needs_an_explicit_recorded_acceptance() -> None:
    accepted = f"{providers.OUTBOUND_READY},{providers.OUTBOUND_PLAIN_SIGNALLING}"

    plan, _ = plan_for(SIP_OUTBOUND_TRANSPORT="tcp", TELEPHONY_CONFIRMED=accepted)

    assert plan.ready
    assert any("unencrypted" in warning for warning in plan.warnings)


def test_plan_output_hides_numbers_and_password() -> None:
    plan, settings = plan_for()

    dumped = json.dumps(plan.as_dict())
    assert MOBILE not in dumped and CALLER_ID not in dumped
    assert PASSWORD not in dumped
    assert PASSWORD not in repr(settings) and PASSWORD not in repr(plan)


def test_trunk_request_carries_credentials_caller_id_tls_srtp_and_india() -> None:
    plan, settings = plan_for()

    trunk = outbound.outbound_trunk_request(plan, settings).trunk

    assert trunk.name == "telugu-outbound-vobiz"
    assert trunk.address == "abc123.sip.vobiz.ai"
    assert list(trunk.numbers) == [CALLER_ID]
    assert (trunk.auth_username, trunk.auth_password) == ("lkoutbound", PASSWORD)
    assert trunk.transport == api.SIPTransport.SIP_TRANSPORT_TLS
    assert trunk.media_encryption == api.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_REQUIRE
    assert trunk.destination_country == "in"


def test_trunk_request_refuses_a_blocked_plan() -> None:
    plan, settings = plan_for(TELEPHONY_CONFIRMED="")

    with pytest.raises(outbound.ProvisioningRefusedError):
        outbound.outbound_trunk_request(plan, settings)


# --- fake LiveKit -----------------------------------------------------------


class FakeLiveKit:
    def __init__(self, trunks=(), rooms=(), *, fail_on="") -> None:
        self.trunks, self.room_names, self.fail_on = list(trunks), list(rooms), fail_on
        self.calls: list[str] = []
        self.dispatches: list[api.CreateAgentDispatchRequest] = []
        self.sip = self.room = self.agent_dispatch = self

    def factory(self, url, api_key, api_secret):
        return self

    async def aclose(self) -> None:
        pass

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if name == self.fail_on:
            raise ConnectionError(f"boom {MOBILE} {PASSWORD}")

    async def list_outbound_trunk(self, request):
        self._record("list_outbound_trunk")
        return api.ListSIPOutboundTrunkResponse(items=self.trunks)

    async def create_outbound_trunk(self, request):
        self._record("create_outbound_trunk")
        trunk = api.SIPOutboundTrunkInfo()
        trunk.CopyFrom(request.trunk)
        trunk.sip_trunk_id = "ST_out"
        self.trunks.append(trunk)
        return trunk

    async def delete_trunk(self, request):
        self._record("delete_trunk")
        self.trunks[:] = [
            t for t in self.trunks if t.sip_trunk_id != request.sip_trunk_id
        ]

    async def list_rooms(self, request):
        self._record("list_rooms")
        return SimpleNamespace(rooms=[SimpleNamespace(name=n) for n in self.room_names])

    async def create_dispatch(self, request):
        self._record("create_dispatch")
        self.dispatches.append(request)

    async def list_participants(self, request):
        self._record("list_participants")
        return SimpleNamespace(participants=[])


def existing_trunk(**overrides) -> api.SIPOutboundTrunkInfo:
    plan, settings = plan_for()
    trunk = api.SIPOutboundTrunkInfo()
    trunk.CopyFrom(outbound.outbound_trunk_request(plan, settings).trunk)
    trunk.sip_trunk_id = "ST_out"
    for name, value in overrides.items():
        setattr(trunk, name, value)
    return trunk


@pytest.fixture
def env(monkeypatch, settings):
    for name in ENV_NAMES:
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
def livekit(monkeypatch):
    def install(fake: FakeLiveKit) -> FakeLiveKit:
        real = outbound.OutboundGateway

        def build(**kwargs):
            return real(**kwargs, api_factory=fake.factory)

        monkeypatch.setattr(f"{COMMAND}.OutboundGateway", build)
        return fake

    return install


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("this command must not contact LiveKit")

    monkeypatch.setattr(f"{COMMAND}.OutboundGateway", refuse)


def run(*args: str) -> str:
    out = io.StringIO()
    call_command("telephony", *args, stdout=out)
    return out.getvalue()


CALL = ("call", "--to", MOBILE, "--yes-place-one-call", "--no-wait")

# --- plan and trunk commands -------------------------------------------------


def test_outbound_plan_is_offline_and_masked(env, no_network) -> None:
    env(**READY_ENV)

    output = run("outbound-plan")

    assert "READY: no blockers" in output
    assert "+91********10" in output
    for secret in (MOBILE, CALLER_ID, PASSWORD):
        assert secret not in output


def test_outbound_apply_dry_run_changes_nothing(env, livekit) -> None:
    env(**READY_ENV)
    fake = livekit(FakeLiveKit())

    output = run("outbound-apply", "--dry-run")

    assert fake.calls == ["list_outbound_trunk"]
    assert "Nothing was changed" in output


def test_outbound_apply_needs_the_explicit_flag(env, no_network) -> None:
    env(**READY_ENV)

    with pytest.raises(CommandError, match="--yes-change-livekit"):
        run("outbound-apply")


def test_outbound_apply_creates_once_and_is_idempotent(env, livekit) -> None:
    env(**READY_ENV)
    fake = livekit(FakeLiveKit())

    first = run("outbound-apply", "--yes-change-livekit")
    second = run("outbound-apply", "--yes-change-livekit")

    assert fake.calls.count("create_outbound_trunk") == 1
    assert "Created outbound trunk ST_out" in first
    assert "already exists and matches" in second
    assert PASSWORD not in first + second


def test_outbound_apply_refuses_a_blocked_plan(env, livekit) -> None:
    env(**{**READY_ENV, "TELEPHONY_CONFIRMED": ""})
    fake = livekit(FakeLiveKit())

    with pytest.raises(CommandError, match="plan is blocked"):
        run("outbound-apply", "--yes-change-livekit")

    assert fake.calls == []


def test_outbound_apply_never_overwrites_a_different_trunk(env, livekit) -> None:
    env(**READY_ENV)
    fake = livekit(FakeLiveKit([existing_trunk(address="other.example.com")]))

    with pytest.raises(CommandError, match="differs from the plan in: address"):
        run("outbound-apply", "--yes-change-livekit")

    assert "create_outbound_trunk" not in fake.calls


def test_create_failure_is_reported_without_secrets(env, livekit) -> None:
    env(**READY_ENV)
    livekit(FakeLiveKit(fail_on="create_outbound_trunk"))

    with pytest.raises(CommandError, match="create_outbound_trunk") as raised:
        run("outbound-apply", "--yes-change-livekit")

    assert PASSWORD not in str(raised.value) and MOBILE not in str(raised.value)


def test_outbound_teardown_deletes_only_our_trunk(env, livekit) -> None:
    env(**READY_ENV)
    other = existing_trunk(sip_trunk_id="ST_other", name="someone-elses")
    fake = livekit(FakeLiveKit([existing_trunk(), other]))

    with pytest.raises(CommandError, match="--yes-delete-from-livekit"):
        run("outbound-teardown")
    assert "ST_out" in run("outbound-teardown", "--dry-run")
    assert len(fake.trunks) == 2

    run("outbound-teardown", "--yes-delete-from-livekit")

    assert [t.sip_trunk_id for t in fake.trunks] == ["ST_other"]


# --- the one-call command ----------------------------------------------------


def test_call_needs_the_explicit_flag(env, no_network) -> None:
    env(**READY_ENV)

    with pytest.raises(CommandError, match="--yes-place-one-call"):
        run("call", "--to", MOBILE)


def test_call_dispatches_one_job_to_the_existing_agent(env, livekit) -> None:
    env(**READY_ENV)
    fake = livekit(FakeLiveKit([existing_trunk()]))

    output = run(*CALL)

    assert fake.calls == ["list_outbound_trunk", "list_rooms", "create_dispatch"]
    (dispatch,) = fake.dispatches
    assert dispatch.agent_name == "telugu-agent"
    assert dispatch.room.startswith("outcall-")
    request = json.loads(dispatch.metadata)["outbound_call"]
    assert request.pop("requested_at") > 0
    assert request == {
        "to": MOBILE,
        "trunk_id": "ST_out",
        "ringing_timeout": 30,
        "max_call_duration": 300,
    }
    assert MOBILE not in output and "+91********10" in output


def test_each_call_gets_its_own_room(env, livekit) -> None:
    from apps.telephony.models import CallRecord

    env(**READY_ENV)
    fake = livekit(FakeLiveKit([existing_trunk()]))

    run(*CALL)
    CallRecord.objects.update(status=CallRecord.Status.COMPLETED)
    run(*CALL)

    first, second = (d.room for d in fake.dispatches)
    assert first != second


@pytest.mark.parametrize("number", ["+919876543211", "+15105550100", CALLER_ID, ""])
def test_any_other_number_is_refused_before_livekit_is_touched(
    env, livekit, number
) -> None:
    env(**READY_ENV)
    fake = livekit(FakeLiveKit([existing_trunk()]))

    with pytest.raises(
        CommandError,
        match=r"not in OUTBOUND_ALLOWLIST_NUMBERS|international format|caller",
    ):
        run("call", "--to", number, "--yes-place-one-call", "--no-wait")

    assert fake.calls == []


def test_call_is_refused_when_the_plan_is_blocked(env, livekit) -> None:
    env(**{**READY_ENV, "TELEPHONY_CONFIRMED": ""})
    fake = livekit(FakeLiveKit([existing_trunk()]))

    with pytest.raises(CommandError, match="plan is blocked"):
        run(*CALL)

    assert fake.dispatches == []


def test_call_is_refused_without_a_trunk(env, livekit) -> None:
    env(**READY_ENV)
    fake = livekit(FakeLiveKit())

    with pytest.raises(CommandError, match="outbound-apply"):
        run(*CALL)

    assert fake.dispatches == []


def test_second_call_is_refused_while_one_is_in_progress(env, livekit) -> None:
    env(**READY_ENV)
    fake = livekit(
        FakeLiveKit([existing_trunk()], rooms=["outcall-abc", "telugu-web-1"])
    )

    with pytest.raises(CommandError, match="already in progress"):
        run(*CALL)

    assert fake.dispatches == []


def test_dispatch_failure_is_reported_and_not_retried(env, livekit) -> None:
    env(**READY_ENV)
    fake = livekit(FakeLiveKit([existing_trunk()], fail_on="create_dispatch"))

    with pytest.raises(CommandError, match="create_dispatch") as raised:
        run(*CALL)

    assert fake.calls.count("create_dispatch") == 1
    assert MOBILE not in str(raised.value)


def test_django_never_dials_and_has_no_loop_over_numbers() -> None:
    app = ROOT / "apps" / "telephony"
    source = "".join(p.read_text(encoding="utf-8") for p in app.rglob("*.py"))

    # The worker dials; Django only dispatches one job for one number.
    assert "create_sip_participant" not in source
    assert source.count('"create_dispatch"') == 1
    assert "retry" not in source.lower().replace("no retry", "").replace(
        "retries", ""
    ).replace("retry-after", "")
