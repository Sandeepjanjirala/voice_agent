"""Offline acceptance probe for the two production images, without pytest.

Mount this file read-only at /tmp/image_compatibility.py and run the image's
Python with ``/tmp/image_compatibility.py backend`` (or ``voice``). Use Docker
``--network none --read-only --tmpfs /tmp --cap-drop ALL`` and resource limits.
The probe uses synthetic data and temporary files, never credentials/providers.
Python socket auditing is an extra guard; container network isolation also
blocks native-library and subprocess traffic. No real calls are exercised.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import io
import json
import os
import platform
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def deny_network(event, _args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname"}:
        raise RuntimeError("Network access is forbidden in the offline image probe")


def standard_library():
    import bz2
    import ctypes
    import gzip
    import lzma
    import socket
    import sqlite3
    import ssl
    import uuid
    import xml.etree.ElementTree as ElementTree
    import zlib
    from datetime import datetime
    from zoneinfo import ZoneInfo

    require(os.getuid() != 0, "Production image must run as a non-root user")
    payload = b"synthetic compatibility data\x00" * 256
    for codec in (bz2, gzip, lzma, zlib):
        require(codec.decompress(codec.compress(payload)) == payload, codec.__name__)
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE probe(value BLOB)")
        connection.execute("INSERT INTO probe VALUES(?)", (payload,))
        require(
            connection.execute("SELECT value FROM probe").fetchone()[0] == payload,
            "SQLite round trip",
        )
    context = ssl.create_default_context()
    require(context.cert_store_stats()["x509_ca"] > 0, "System CA certificates missing")
    require(context.minimum_version >= ssl.TLSVersion.TLSv1_2, "TLS minimum version")
    callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)(lambda value: value + 1)
    require(callback(41) == 42, "libffi callback")
    require(uuid.uuid4().version == 4 and uuid.uuid1().version == 1, "UUID generation")
    require(
        ElementTree.fromstring("<probe>ok</probe>").text == "ok", "Expat XML parser"
    )
    require(
        datetime(2026, 1, 1, tzinfo=ZoneInfo("Asia/Kolkata"))
        .utcoffset()
        .total_seconds()
        == 19800,
        "Time zone database",
    )
    left, right = socket.socketpair()
    with left, right:
        left.sendall(payload)
        require(right.recv(len(payload)) == payload, "Local socket IPC")
    return {
        "openssl": ssl.OPENSSL_VERSION,
        "sqlite": sqlite3.sqlite_version,
        "ca_certificates": context.cert_store_stats()["x509_ca"],
        "libc": platform.libc_ver(),
    }


def process_ipc():
    import multiprocessing

    context = multiprocessing.get_context("fork")
    semaphore = context.Semaphore(1)
    require(semaphore.acquire(timeout=1), "Process semaphore")
    semaphore.release()
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=writer.send, args=("synthetic-child",))
    try:
        process.start()
        require(reader.poll(10), "Child process did not respond")
        require(reader.recv() == "synthetic-child", "Process IPC")
        process.join(timeout=10)
        require(process.exitcode == 0, "Child process failed")
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        reader.close()
        writer.close()
    return {"start_method": "fork", "semaphore": True}


def control_plane():
    import aiohttp
    from livekit import api

    token = (
        api.AccessToken("synthetic-api-key", secrets.token_hex(32))
        .with_identity("offline-probe")
        .with_grants(api.VideoGrants(room_join=True, room="synthetic-room"))
        .to_jwt()
    )
    require(len(token.split(".")) == 3, "LiveKit token serialization")
    parser = aiohttp.http_parser.HttpResponseParserPy(None, None, 8190)
    require(parser is not None, "HTTP parser import")
    return {
        "livekit-api": importlib.metadata.version("livekit-api"),
        "aiohttp": aiohttp.__version__,
    }


def backend_libraries():
    import psycopg
    from cryptography.fernet import Fernet
    from kombu.serialization import dumps, loads
    from openpyxl import Workbook, load_workbook
    from redis.connection import Connection

    cipher = Fernet(Fernet.generate_key())
    require(
        cipher.decrypt(cipher.encrypt(b"synthetic recording"))
        == b"synthetic recording",
        "Recording crypto round trip",
    )
    require(psycopg.pq.version() >= 140000, "Bundled PostgreSQL client library")
    require(
        Connection().pack_command("PING") == [b"*1\r\n$4\r\nPING\r\n"],
        "Redis RESP encoding",
    )
    content_type, encoding, message = dumps({"synthetic": True}, serializer="json")
    require(
        loads(message, content_type, encoding) == {"synthetic": True},
        "Celery message serialization",
    )
    workbook = Workbook()
    workbook.active.append(["synthetic", 42])
    stream = io.BytesIO()
    workbook.save(stream)
    stream.seek(0)
    loaded = load_workbook(stream, read_only=True)
    require(loaded.active["B1"].value == 42, "XLSX zip/XML round trip")
    loaded.close()
    for name in ("gunicorn.app.wsgiapp", "whitenoise", "boto3", "celery"):
        importlib.import_module(name)
    return {"libpq": psycopg.pq.version(), "psycopg": psycopg.__version__}


def backend_application():
    os.environ.update(
        {
            "SKIP_ENV_LOCAL": "1",
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "DJANGO_SECRET_KEY": "synthetic-offline-image-probe-never-use-in-production",
            "DJANGO_DEBUG": "false",
            "DEV_AUTO_LOGIN_USER": "",
            "DATABASE_URL": "sqlite://:memory:",
            "DJANGO_ALLOWED_HOSTS": "localhost",
        }
    )
    import django
    from django.db import connection

    django.setup()
    from config.wsgi import application

    require(callable(application), "Django WSGI application")
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        require(cursor.fetchone() == (1,), "Django database adapter")
    # Import the real outbound/control-plane paths; never instantiate a client
    # or invoke a task, dial operation, upload or provider request.
    for name in (
        "apps.telephony.providers",
        "apps.telephony.livekit_gateway",
        "apps.calling.recordings",
        "apps.operations.tasks",
    ):
        importlib.import_module(name)
    static = Path("/app/staticfiles/admin/css/base.css")
    require(
        static.is_file() and static.stat().st_size > 1000,
        "Collected Django admin CSS missing",
    )
    require(
        importlib.util.find_spec("livekit.agents") is None,
        "Voice runtime leaked into backend image",
    )
    return {"django": django.get_version(), "static_bytes": static.stat().st_size}


def backup_commands():
    with tempfile.TemporaryDirectory(prefix="vaani-compat-") as directory:
        root = Path(directory)
        (root / "synthetic.bin").write_bytes(b"encrypted-fixture-only")
        result = subprocess.run(
            [
                "sh",
                "-c",
                'cp "$1/synthetic.bin" "$1/copy.bin" && test -f "$1/copy.bin" && find "$1" -name copy.bin -print',
                "probe",
                directory,
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
        require(result.stdout.strip(), "Recovery shell/cp/find/test")
        archive = subprocess.run(
            ["tar", "-C", directory, "-czf", "-", "synthetic.bin"],
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout
        result = subprocess.run(
            ["tar", "-xzOf", "-", "synthetic.bin"],
            input=archive,
            check=True,
            capture_output=True,
            timeout=10,
        )
        require(
            result.stdout == b"encrypted-fixture-only",
            "Recording archive tar/gzip round trip",
        )
    return {"shell_and_archive_round_trip": True}


def voice_audio():
    import av
    import numpy as np
    from livekit import rtc

    frame = rtc.AudioFrame.create(
        sample_rate=48000, num_channels=1, samples_per_channel=4800
    )
    resampler = rtc.AudioResampler(48000, 16000)
    output = [*resampler.push(frame), *resampler.flush()]
    require(
        sum(item.samples_per_channel for item in output) == 1600,
        "Native RTC/Sox resampling",
    )
    require(all(item.sample_rate == 16000 for item in output), "RTC output sample rate")
    stream = io.BytesIO()
    with av.open(stream, "w", format="ogg") as container:
        track = container.add_stream("libopus", rate=48000)
        track.layout = "mono"
        audio = av.AudioFrame.from_ndarray(
            np.zeros((1, 4800), dtype=np.int16), format="s16", layout="mono"
        )
        audio.sample_rate = 48000
        audio.pts = 0
        for packet in track.encode(audio):
            container.mux(packet)
        for packet in track.encode(None):
            container.mux(packet)
    stream.seek(0)
    with av.open(stream) as container:
        samples = sum(frame.samples for frame in container.decode(audio=0))
    require(samples == 4800, "PyAV/FFmpeg Opus encode/decode")
    return {
        "livekit": importlib.metadata.version("livekit"),
        "av": av.__version__,
        "resampled_samples": 1600,
        "opus_decoded_samples": samples,
    }


def voice_plugins():
    os.environ.update(
        {
            "SKIP_ENV_LOCAL": "1",
            "SARVAM_API_KEY": "synthetic-offline-probe",
            "LLM_PROVIDER": "livekit",
        }
    )
    from livekit.plugins import ai_coustics, openai, sarvam
    from livekit.plugins.ai_coustics import _ffi

    parameters = _ffi.ModelParameters(bypass=0.0, enhancement_level=1.0)
    require(
        _ffi.model_parameters_equal(parameters, parameters),
        "ai-coustics native FFI call",
    )
    require(
        ai_coustics.EnhancerModel.QUAIL_VF_S is not None, "Configured enhancer model"
    )
    require(
        callable(openai.LLM) and callable(sarvam.STT), "Configured provider plugins"
    )
    import voice_agent.agent

    require(voice_agent.agent is not None, "Application entrypoint import")
    require(
        importlib.util.find_spec("django") is None, "Django leaked into voice image"
    )
    # The installed plugins currently need no separate local model downloads.
    # Re-running the build-time command with no network and a read-only root
    # detects a future plugin requiring uncached assets. Container isolation is
    # mandatory because this child interpreter does not inherit the audit hook.
    subprocess.run(
        [sys.executable, "-m", "livekit.agents", "download-files"],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return {
        "livekit-agents": importlib.metadata.version("livekit-agents"),
        "ai-coustics": importlib.metadata.version("livekit-plugins-ai-coustics"),
        "offline_download_files": True,
        "provider_calls": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", choices=("backend", "voice"))
    args = parser.parse_args()
    sys.path.insert(0, "/app")
    sys.addaudithook(deny_network)
    checks = [standard_library, process_ipc, control_plane]
    checks += (
        [backend_libraries, backend_application, backup_commands]
        if args.runtime == "backend"
        else [voice_audio, voice_plugins]
    )
    results = {}
    for check in checks:
        try:
            results[check.__name__] = {"ok": True, **check()}
        except (Exception, SystemExit) as error:
            results[check.__name__] = {
                "ok": False,
                "error": f"{type(error).__name__}: {error}",
            }
    ok = all(result["ok"] for result in results.values())
    print(
        json.dumps(
            {
                "ok": ok,
                "runtime": args.runtime,
                "python": platform.python_version(),
                "uid": os.getuid(),
                "checks": results,
            },
            indent=2,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
