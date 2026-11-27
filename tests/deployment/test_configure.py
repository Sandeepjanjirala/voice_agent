"""Production configuration safety, using synthetic keys and temporary files."""

import importlib.util
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/configure.py"
SPEC = importlib.util.spec_from_file_location("deployment_configure", SCRIPT)
configure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure)


def cli(*arguments):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, arguments)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


@pytest.fixture
def environment(tmp_path):
    path = tmp_path / "backend.env"
    result = cli(
        "init", "--env-file", path, "--domain", "voice.example.org", "--port", 8123
    )
    assert result.returncode == 0, result.stderr
    return path, configure.read_env(path)


def write_values(path, values):
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8"
    )
    path.chmod(0o600)


def test_fresh_install_generates_independent_working_keys_and_safe_defaults(
    environment,
):
    path, values = environment
    assert len({values[key] for key in configure.KEY_NAMES}) == len(configure.KEY_NAMES)
    for name in ("CONTACT_ENCRYPTION_KEYS", "RECORDING_ENCRYPTION_KEYS"):
        cipher = Fernet(values[name].encode())
        assert (
            cipher.decrypt(cipher.encrypt(b"synthetic fixture")) == b"synthetic fixture"
        )
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", values[name])
        for name in ("POSTGRES_PASSWORD", "REDIS_PASSWORD", "CONTACT_LOOKUP_KEY")
    )
    assert values["OUTBOUND_DIALING_MODE"] == "allowlist"
    assert values["CONTACT_DIALING_ENABLED"] == "false"
    assert not values["OUTBOUND_ALLOWLIST_NUMBERS"]
    assert not values["SIP_INBOUND_NUMBERS"]
    assert not values["TELEPHONY_CONFIRMED"]
    assert values["DJANGO_ALLOWED_HOSTS"] == "voice.example.org"
    assert values["DJANGO_CSRF_TRUSTED_ORIGINS"] == "https://voice.example.org"
    assert values["APP_PORT"] == "8123"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert cli("validate", "--env-file", path).returncode == 0


def test_repeat_initialisation_preserves_every_existing_byte_and_key(environment):
    path, _ = environment
    before = path.read_bytes()
    result = cli("init", "--env-file", path, "--domain", "another.example.org")
    assert result.returncode == 0
    assert path.read_bytes() == before


def test_invalid_existing_environment_is_never_replaced(environment):
    path, values = environment
    values["POSTGRES_PASSWORD"] = "existing-short-key"
    write_values(path, values)
    before = path.read_bytes()
    result = cli("init", "--env-file", path)
    assert result.returncode == 2
    assert path.read_bytes() == before
    assert "existing-short-key" not in result.stdout + result.stderr


def test_callbacks_use_the_selected_production_domain(environment):
    _, values = environment
    for key, route in configure.CALLBACKS.items():
        assert values[key] == f"https://voice.example.org/api/telephony/{route}/"


@pytest.mark.parametrize("key", configure.CALLBACKS)
def test_local_or_stale_callback_is_rejected_without_echoing_it(environment, key):
    path, values = environment
    sentinel = "private-callback-diagnostic-sentinel"
    values[key] = f"http://127.0.0.1:8000/{sentinel}/"
    write_values(path, values)
    result = cli("validate", "--env-file", path)
    assert result.returncode == 2
    assert key in result.stderr
    assert sentinel not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DJANGO_DEBUG", "true"),
        ("DJANGO_SECURE_COOKIES", "false"),
        ("DJANGO_ALLOWED_HOSTS", "*"),
        ("DEV_AUTO_LOGIN_USER", "private-user-fixture"),
        ("CONTACT_ENCRYPTION_KEYS", "x" * 44),
        ("RECORDING_LOCAL_DIR", "/app/staticfiles/recordings"),
        ("POSTGRES_PASSWORD", "private$unsafe-password-diagnostic-sentinel"),
    ],
)
def test_invalid_production_settings_fail_without_echoing_values(
    environment, key, value
):
    path, values = environment
    values[key] = value
    write_values(path, values)
    result = cli("validate", "--env-file", path)
    assert result.returncode == 2
    for secret in (values[name] for name in configure.KEY_NAMES):
        assert secret not in result.stdout + result.stderr
    assert "private-user-fixture" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("LIVEKIT_URL", "wss://[private-malformed-sentinel"),
        ("LIVEKIT_URL", "ws://private-insecure-sentinel.example.org"),
        ("LLM_BASE_URL", "https://user:private-password-sentinel@example.org"),
        ("LIVEKIT_INFERENCE_URL", "https://example.org:private-port-sentinel"),
    ],
)
def test_bad_provider_urls_fail_without_a_traceback_or_secret_echo(
    environment, key, value
):
    path, values = environment
    values[key] = value
    write_values(path, values)
    result = cli("validate", "--env-file", path)
    assert result.returncode == 2
    assert key in result.stderr
    assert value not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_get_accepts_the_argument_order_used_by_bash_helpers(environment):
    path, _ = environment
    result = cli("get", "--env-file", path, "SITE_DOMAIN")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "voice.example.org"


@pytest.mark.parametrize("name", configure.KEY_NAMES)
def test_get_never_prints_secrets(environment, name):
    path, values = environment
    result = cli("get", "--env-file", path, name)
    assert result.returncode == 2
    assert values[name] not in result.stdout + result.stderr


def test_environment_text_is_data_and_duplicate_names_are_rejected(tmp_path):
    path = tmp_path / "literal.env"
    payload = "$(touch should-never-be-executed)"
    path.write_text(f"TOKEN='{payload}'\n", encoding="utf-8")
    assert configure.read_env(path) == {"TOKEN": payload}
    path.write_text("TOKEN=first\nTOKEN=private-duplicate-sentinel\n", encoding="utf-8")
    with pytest.raises(configure.ConfigError, match="Duplicate") as caught:
        configure.read_env(path)
    assert "private-duplicate-sentinel" not in str(caught.value)


def test_worker_export_preserves_callbacks_and_excludes_backend_cloud_secrets(
    environment, tmp_path
):
    path, values = environment
    values.update(
        LIVEKIT_URL="wss://fixture.livekit.cloud",
        LIVEKIT_API_KEY="private-cloud-key-sentinel",
        LIVEKIT_API_SECRET="private-cloud-secret-sentinel",
        GEMINI_API_KEY="private-dashboard-assistant-sentinel",
        SIP_OUTBOUND_AUTH_PASSWORD="private-sip-password-sentinel",
        SARVAM_API_KEY="private-sarvam-key-sentinel",
        SARVAM_TTS_PACE="1.07",
    )
    write_values(path, values)
    output = tmp_path / "worker.env"
    result = cli("worker-env", "--env-file", path, "--output", output)
    assert result.returncode == 0, result.stderr
    worker = configure.read_env(output)
    assert worker["SARVAM_API_KEY"] == values["SARVAM_API_KEY"]
    assert worker["SARVAM_TTS_PACE"] == "1.07"
    assert worker["CALL_EVENTS_SECRET"] == values["CALL_EVENTS_SECRET"]
    for key in configure.CALLBACKS:
        assert worker[key] == values[key]
    forbidden = {
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "DJANGO_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "REDIS_PASSWORD",
        "CONTACT_ENCRYPTION_KEYS",
        "CONTACT_LOOKUP_KEY",
        "RECORDING_ENCRYPTION_KEYS",
        "GEMINI_API_KEY",
        "SIP_OUTBOUND_AUTH_PASSWORD",
    }
    assert forbidden.isdisjoint(worker)
    assert set(worker) <= set(values), "Export must not invent example voice tuning."
    for key in forbidden | {"SARVAM_API_KEY"}:
        assert values[key] not in result.stdout + result.stderr
    before = output.read_bytes()
    assert cli("worker-env", "--env-file", path, "--output", output).returncode == 2
    assert output.read_bytes() == before


def test_https_candidate_protects_private_paths_and_overwrites_proxy_headers(
    environment, tmp_path
):
    path, _ = environment
    candidate = tmp_path / "voice.conf"
    result = cli("nginx", "--env-file", path, "--output", candidate)
    assert result.returncode == 0, result.stderr
    text = candidate.read_text(encoding="utf-8")
    assert "@@" not in text
    assert "proxy_pass http://127.0.0.1:8123;" in text
    assert "server_name voice.example.org;" in text
    assert "location ^~ /api/health/ready { return 404; }" in text
    assert "proxy_set_header X-Forwarded-Proto https;" in text
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in text
    assert 'proxy_set_header Forwarded "";' in text
    assert 'proxy_set_header CF-Connecting-IP "";' in text
    assert "$proxy_add_x_forwarded_for" not in text
    assert not re.search(r"^\s*alias\s", text, flags=re.MULTILINE)
    assert "client_max_body_size 64m;" in text
    assert "microphone=(self)" in text
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in text
    assert "return 301 https://voice.example.org$request_uri;" in text
    assert "/etc/letsencrypt/live/voice.example.org/fullchain.pem" in text
    before = candidate.read_bytes()
    assert cli("nginx", "--env-file", path, "--output", candidate).returncode == 2
    assert candidate.read_bytes() == before


def test_http_bootstrap_has_only_acme_challenge_and_https_redirect(
    environment, tmp_path
):
    path, _ = environment
    candidate = tmp_path / "http.conf"
    result = cli("nginx", "--env-file", path, "--output", candidate, "--http-only")
    assert result.returncode == 0
    text = candidate.read_text(encoding="utf-8")
    assert "/.well-known/acme-challenge/" in text
    assert "proxy_pass" not in text
    assert "ssl_certificate" not in text
    assert "return 301 https://voice.example.org$request_uri;" in text


def test_nginx_rejects_certificate_path_injection(environment, tmp_path):
    path, _ = environment
    candidate = tmp_path / "injected.conf"
    result = cli(
        "nginx",
        "--env-file",
        path,
        "--output",
        candidate,
        "--certificate-name",
        "../../edunexa; include /tmp/unsafe;",
    )
    assert result.returncode == 2
    assert not candidate.exists()


@pytest.mark.parametrize(
    "domain",
    [
        "localhost",
        "127.0.0.1",
        "voice.local",
        "https://voice.example.org",
        "voice.example.org/unsafe",
        "voice.example.org; include /tmp/unsafe;",
        "a" * 64 + ".example.org",
    ],
)
def test_invalid_hostname_cannot_generate_configuration(domain, tmp_path):
    path = tmp_path / "invalid.env"
    result = cli("init", "--env-file", path, "--domain", domain)
    assert result.returncode == 2
    assert not path.exists()


def test_symlink_environment_is_never_followed(environment, tmp_path):
    path, _ = environment
    link = tmp_path / "link.env"
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip("Creating symlinks requires OS privileges on this platform.")
    before = path.read_bytes()
    assert cli("init", "--env-file", link).returncode == 2
    assert path.read_bytes() == before
