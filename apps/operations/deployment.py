"""Local deployment probes. No LiveKit calls, task execution or personal data.

The shell deployment guard stops admission and workers, then repeats the idle
check without worker RPC. These snapshots alone are not a distributed lock.
"""

import json
import os
import stat
import tempfile
import uuid
from pathlib import Path

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Q
from django.utils import timezone

BEAT_HEARTBEAT_KEY = "deployment:beat-heartbeat"
BEAT_MAX_AGE = 120
RPC_TIMEOUT = 3.0
QUEUE_SCAN_LIMIT = 1000


class ProbeError(Exception):
    """An operator-facing reason written here, never a connection exception."""


def _redis():
    import redis

    if not settings.REDIS_URL:
        raise ValueError("Redis is required in production.")
    return redis.Redis.from_url(
        settings.REDIS_URL, socket_timeout=3, socket_connect_timeout=3
    )


def readiness() -> dict:
    from apps.crm import crypto

    checks = {}
    probes = {
        "database": _database,
        "migrations": _migrations,
        "cache": _cache,
        "job_broker": lambda: bool(_redis().ping()),
        "contact_keys": crypto.configured,
        "background_jobs": lambda: not settings.CELERY_TASK_ALWAYS_EAGER,
    }
    for name, probe in probes.items():
        try:
            checks[name] = bool(probe())
        except Exception:
            checks[name] = False
    return {"ok": all(checks.values()), **checks}


def _database() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        return cursor.fetchone()[0] == 1


def _migrations() -> bool:
    executor = MigrationExecutor(connection)
    return not executor.migration_plan(executor.loader.graph.leaf_nodes())


def _cache() -> bool:
    key = f"deployment:probe:{uuid.uuid4().hex}"
    try:
        cache.set(key, 1, timeout=10)
        return cache.get(key) == 1
    finally:
        cache.delete(key)


def storage(*, prepare=False) -> dict:
    """Check private local storage with a tiny synthetic encrypted temporary file.

    No recording is read or deleted. Existing permissions are never changed.
    S3 checks configuration only: no paid or external storage operations.
    """
    try:
        keys = [Fernet(key.encode()) for key in settings.RECORDING_ENCRYPTION_KEYS]
        valid_keys = bool(keys)
    except (ValueError, TypeError):
        keys, valid_keys = [], False
    result = {
        "ok": False,
        "recording_keys": valid_keys,
        "backend": settings.RECORDING_STORAGE,
    }
    if settings.RECORDING_STORAGE == "s3":
        result["configured"] = bool(settings.RECORDING_S3_BUCKET)
        result["remote_verified"] = False
        result["ok"] = valid_keys and result["configured"]
        return result
    base = Path(settings.RECORDING_LOCAL_DIR)
    # A symlink can redirect preparation outside the private mount.
    if base.is_symlink():
        return {**result, "private_permissions": False, "writable": False}
    if prepare:
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not base.is_dir():
        return {**result, "private_permissions": False, "writable": False}
    private = os.name == "nt" or stat.S_IMODE(base.stat().st_mode) & 0o077 == 0
    # Refuse a configuration that would place audio below publicly served static.
    resolved = base.resolve()
    static = Path(settings.STATIC_ROOT).resolve()
    private = private and resolved != static and static not in resolved.parents
    writable = False
    if private and valid_keys:
        payload = keys[0].encrypt(b"deployment-storage-probe")
        with tempfile.TemporaryFile(dir=base) as handle:
            handle.write(payload)
            handle.flush()
            handle.seek(0)
            writable = keys[0].decrypt(handle.read()) == b"deployment-storage-probe"
    result.update(private_permissions=private, writable=writable)
    result["ok"] = valid_keys and private and writable
    return result


def worker() -> dict:
    from config.celery import app

    if not settings.REDIS_URL or settings.CELERY_TASK_ALWAYS_EAGER:
        return {"ok": False, "responding": 0}
    replies = app.control.inspect(timeout=RPC_TIMEOUT).ping() or {}
    responding = sum(reply.get("ok") == "pong" for reply in replies.values())
    return {"ok": responding > 0, "responding": responding}


def beat() -> dict:
    stamp = cache.get(BEAT_HEARTBEAT_KEY)
    age = (
        timezone.now().timestamp() - stamp if isinstance(stamp, (float, int)) else None
    )
    return {
        "ok": age is not None and 0 <= age <= BEAT_MAX_AGE,
        "age_seconds": round(age, 1) if age is not None else None,
    }


def _broker_work() -> dict:
    """Read the installed Kombu Redis transport's queues, including priorities.

    The broker's unacked hash also covers work reserved before a worker crash.
    Periodic queued tasks can be reconstructed from DB; active tasks still block.
    Unknown tasks or an excessive backlog fail closed. No arguments are output.
    """
    from config.celery import app

    periodic = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
    counts = {"queued_jobs": 0, "unacked_jobs": 0}
    scanned = 0
    with (
        app.connection_for_read(connect_timeout=3) as broker,
        broker.channel() as channel,
    ):
        for queue in app.amqp.queues:
            for priority in channel.priority_steps:
                name = channel._q_for_pri(queue, priority)
                size = channel.client.llen(name)
                scanned += size
                if scanned > QUEUE_SCAN_LIMIT:
                    raise ProbeError(
                        "Broker backlog exceeds the safe inspection limit; drain or review the queue."
                    )
                for raw in channel.client.lrange(name, 0, -1):
                    task = json.loads(raw).get("headers", {}).get("task")
                    counts["queued_jobs"] += task not in periodic
        unacked = channel.client.hlen(channel.unacked_key)
        if scanned + unacked > QUEUE_SCAN_LIMIT:
            raise ProbeError(
                "Unacknowledged work exceeds the safe inspection limit; review worker recovery."
            )
        for _, raw in channel.client.hscan_iter(channel.unacked_key):
            message = json.loads(raw)[0]
            task = message.get("headers", {}).get("task")
            counts["unacked_jobs"] += task != "operations.deployment_heartbeat"
    return counts


def inflight(*, offline_workers=False) -> dict:
    from apps.calling.models import CallList, CallListEntry, CallRecording, CallResult
    from apps.telephony.models import CallRecord
    from config.celery import app

    from .models import JobRun

    counts = {
        "active_calls": CallRecord.objects.filter(status__in=CallRecord.ACTIVE).count(),
        "running_lists": CallList.objects.filter(
            status=CallList.Status.RUNNING
        ).count(),
        "calling_entries": CallListEntry.objects.filter(
            status=CallListEntry.Status.CALLING
        ).count(),
        "pending_analysis": CallResult.objects.filter(
            Q(status=CallResult.Status.PENDING)
            | Q(status=CallResult.Status.FAILED, next_attempt_at__isnull=False)
            | Q(status=CallResult.Status.DONE, actions_applied_at__isnull=True)
        ).count(),
        "pending_recordings": CallRecording.objects.filter(
            status__in=[CallRecording.Status.RECORDING, CallRecording.Status.PROCESSING]
        ).count(),
        "running_jobs": JobRun.objects.filter(status=JobRun.Status.RUNNING).count(),
    }
    counts.update(_broker_work())
    if not offline_workers:
        inspector = app.control.inspect(timeout=RPC_TIMEOUT)
        for method in ("active", "reserved", "scheduled"):
            replies = getattr(inspector, method)()
            if not replies:
                raise ProbeError(
                    "Workers did not respond; idle state is unknown. Check worker health before updating."
                )
            counts[f"worker_{method}"] = sum(
                task.get("request", task).get("name")
                != "operations.deployment_heartbeat"
                for tasks in replies.values()
                for task in tasks
            )
    return {"ok": True, "idle": not any(counts.values()), **counts}
