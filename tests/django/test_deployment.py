"""Deployment guards use synthetic state and a mocked broker; no provider calls."""

import io
import json
import os
from contextlib import nullcontext
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.calling.models import CallList, CallListEntry, CallRecording, CallResult
from apps.operations import deployment, tasks
from apps.operations.models import JobRun
from apps.telephony.models import CallRecord
from apps.tenancy.models import Organisation

pytestmark = pytest.mark.django_db


def command(*args, fails=False):
    out = io.StringIO()
    if fails:
        with pytest.raises(CommandError):
            call_command("deploycheck", *args, stdout=out)
    else:
        call_command("deploycheck", *args, stdout=out)
    return json.loads(out.getvalue())


@pytest.fixture
def storage(settings, tmp_path):
    settings.RECORDING_LOCAL_DIR = tmp_path / "private"
    settings.RECORDING_STORAGE = "local"
    settings.RECORDING_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
    return settings.RECORDING_LOCAL_DIR


def test_prepare_private_storage_without_services(storage):
    assert not storage.exists()
    assert command("--prepare-storage")["ok"]
    assert command("--prepare-storage")["ok"]
    assert not list(storage.iterdir())
    if os.name != "nt":
        assert storage.stat().st_mode & 0o777 == 0o700


def test_missing_storage_and_invalid_keys_fail(storage, settings):
    assert not command("--check", "storage", fails=True)["ok"]
    settings.RECORDING_ENCRYPTION_KEYS = ["invalid-secret-must-not-appear"]
    result = command("--prepare-storage", fails=True)
    assert not result["checks"]["storage"]["recording_keys"]
    assert "invalid-secret" not in json.dumps(result)


def test_public_static_directory_cannot_hold_recordings(storage, settings):
    settings.STATIC_ROOT = storage.parent
    assert not command("--prepare-storage", fails=True)["ok"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions tested in Linux CI")
def test_storage_refuses_unsafe_permissions_without_changing_them(storage):
    storage.mkdir(mode=0o755)
    assert not command("--prepare-storage", fails=True)["ok"]
    assert storage.stat().st_mode & 0o777 == 0o755


def test_storage_probe_leaves_existing_encrypted_data_untouched(storage):
    command("--prepare-storage")
    old = storage / "original.ogg.enc"
    old.write_bytes(b"original-encrypted-payload")
    assert command("--prepare-storage")["ok"]
    assert old.read_bytes() == b"original-encrypted-payload"
    assert list(storage.iterdir()) == [old]


def test_s3_is_configuration_only_no_external_probe(settings, monkeypatch):
    settings.RECORDING_STORAGE = "s3"
    settings.RECORDING_S3_BUCKET = "synthetic-bucket"
    settings.RECORDING_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
    monkeypatch.setattr("boto3.client", Mock(side_effect=AssertionError("No network")))
    checks = command("--check", "storage")["checks"]["storage"]
    assert checks["ok"] and not checks["remote_verified"]


def test_dependency_readiness_detects_missing_migrations(monkeypatch, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = False
    monkeypatch.setattr(deployment, "_redis", lambda: Mock(ping=lambda: True))
    assert command("--check", "readiness")["ok"]
    monkeypatch.setattr(deployment, "_migrations", lambda: False)
    result = command("--check", "readiness", fails=True)
    assert not result["checks"]["readiness"]["migrations"]


def test_probe_failure_never_outputs_connection_secrets(monkeypatch):
    monkeypatch.setattr(
        deployment, "worker", Mock(side_effect=RuntimeError("redis://password@host"))
    )
    result = command("--check", "worker", fails=True)
    assert result["checks"]["worker"] == {"ok": False, "error": "RuntimeError"}
    assert "password" not in json.dumps(result)


def test_worker_probe_requires_response_and_real_queue(monkeypatch, settings):
    from config.celery import app

    settings.REDIS_URL = "redis://synthetic.invalid/0"
    settings.CELERY_TASK_ALWAYS_EAGER = False
    inspector = Mock()
    monkeypatch.setattr(app.control, "inspect", lambda **kw: inspector)
    inspector.ping.return_value = {}
    assert not command("--check", "worker", fails=True)["ok"]
    inspector.ping.return_value = {"worker": {"ok": "pong"}}
    assert command("--check", "worker")["checks"]["worker"]["responding"] == 1


def test_beat_heartbeat_missing_fresh_expired_and_cache_loss():
    cache.clear()
    assert not command("--check", "beat", fails=True)["ok"]
    tasks.deployment_heartbeat()
    assert command("--check", "beat")["ok"]
    cache.set(deployment.BEAT_HEARTBEAT_KEY, timezone.now().timestamp() - 121)
    assert not command("--check", "beat", fails=True)["ok"]
    cache.clear()
    assert not deployment.beat()["ok"]
    tasks.deployment_heartbeat()
    assert deployment.beat()["ok"]


@pytest.fixture
def idle_broker(monkeypatch):
    monkeypatch.setattr(
        deployment, "_broker_work", lambda: {"queued_jobs": 0, "unacked_jobs": 0}
    )


def test_idle_guard_and_active_call_are_read_only(idle_broker):
    flags = ("--check", "inflight", "--require-idle", "--offline-workers")
    assert command(*flags)["ok"]
    record = CallRecord.objects.create(room="synthetic-room", masked_number="masked")
    result = command(*flags, fails=True)
    assert result["checks"]["inflight"]["active_calls"] == 1
    record.refresh_from_db()
    assert record.is_active
    assert "synthetic-room" not in json.dumps(result)


@pytest.mark.parametrize(
    "kind", ["list", "entry", "analysis", "retry", "actions", "recording", "job"]
)
def test_idle_guard_protects_important_database_work(idle_broker, kind):
    org = Organisation.objects.create(name="Synthetic")
    record = CallRecord.objects.create(
        room="done", masked_number="masked", organisation=org, status="completed"
    )
    if kind in ("list", "entry"):
        call_list = CallList.objects.create(
            organisation=org,
            name="Test",
            status="running" if kind == "list" else "paused",
        )
        if kind == "entry":
            CallListEntry.objects.create(
                call_list=call_list, position=0, status="calling"
            )
    elif kind in ("analysis", "retry", "actions"):
        CallResult.objects.create(
            call=record,
            status={"analysis": "pending", "retry": "failed", "actions": "done"}[kind],
            next_attempt_at=timezone.now(),
        )
    elif kind == "recording":
        CallRecording.objects.create(call=record, organisation=org, status="processing")
    else:
        JobRun.objects.create(name="retention")
    result = command(
        "--check", "inflight", "--require-idle", "--offline-workers", fails=True
    )
    assert not result["checks"]["inflight"]["idle"]


def test_idle_guard_fails_when_worker_state_unknown(idle_broker, monkeypatch):
    from config.celery import app

    inspector = Mock()
    inspector.active.return_value = None
    monkeypatch.setattr(app.control, "inspect", lambda **kw: inspector)
    assert not command("--check", "inflight", "--require-idle", fails=True)["ok"]


def test_idle_guard_includes_worker_scheduled_and_reserved_jobs(
    idle_broker, monkeypatch
):
    from config.celery import app

    inspector = Mock()
    inspector.active.return_value = {"worker": []}
    inspector.reserved.return_value = {"worker": [{"name": "operations.analyse_call"}]}
    inspector.scheduled.return_value = {
        "worker": [{"request": {"name": "operations.analyse_call"}}]
    }
    monkeypatch.setattr(app.control, "inspect", lambda **kw: inspector)
    result = command("--check", "inflight", "--require-idle", fails=True)["checks"][
        "inflight"
    ]
    assert result["worker_reserved"] == result["worker_scheduled"] == 1


def test_broker_guard_covers_priority_queues_and_unacknowledged_work(monkeypatch):
    from config.celery import app

    messages = {
        "celery": [
            json.dumps({"headers": {"task": "operations.deployment_heartbeat"}})
        ],
        "celery:9": [json.dumps({"headers": {"task": "operations.analyse_call"}})],
    }
    redis = Mock()
    redis.llen.side_effect = lambda key: len(messages[key])
    redis.lrange.side_effect = lambda key, *args: messages[key]
    redis.hlen.return_value = 1
    redis.hscan_iter.return_value = [
        (
            "delivery",
            json.dumps(
                [{"headers": {"task": "operations.advance_call_lists"}}, "", "celery"]
            ),
        )
    ]
    channel = Mock(client=redis, priority_steps=[0, 9], unacked_key="unacked")
    channel._q_for_pri.side_effect = lambda queue, priority: (
        f"{queue}:{priority}" if priority else queue
    )
    broker = Mock()
    broker.channel.return_value = nullcontext(channel)
    monkeypatch.setattr(app, "connection_for_read", lambda **kw: nullcontext(broker))
    assert deployment._broker_work() == {"queued_jobs": 1, "unacked_jobs": 1}


def test_idle_flags_cannot_silently_skip_the_guard():
    with pytest.raises(CommandError, match="needs --check inflight"):
        call_command("deploycheck", "--require-idle")
