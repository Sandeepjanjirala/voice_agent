"""Stdlib-only configuration tooling, run inside the bootstrap Python container.

Environment files are data, never executable shell code. Existing files and
encryption keys are never overwritten; diagnostics do not print secret values.
"""

import argparse
import base64
import ipaddress
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOMAIN = "voice.sandeepwebsolutions.in"
KEY_NAMES = (
    "DJANGO_SECRET_KEY",
    "POSTGRES_PASSWORD",
    "REDIS_PASSWORD",
    "CONTACT_ENCRYPTION_KEYS",
    "CONTACT_LOOKUP_KEY",
    "RECORDING_ENCRYPTION_KEYS",
    "CALL_EVENTS_SECRET",
)
PUBLIC_NAMES = {
    "SITE_DOMAIN",
    "APP_PORT",
    "COMPOSE_PROJECT_NAME",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "BACKUP_KEEP_DAYS",
    "BACKUP_DIR",
    "DEPLOY_MIN_AVAILABLE_MB",
    "DEPLOY_MIN_FREE_GB",
    "DEPLOY_MIN_BUILD_GB",
    "DEPLOY_MAX_SWAP_PERCENT",
    "DEPLOY_PUBLIC_HTTPS",
    "ACME_EMAIL",
    "GUNICORN_WORKERS",
    "GUNICORN_THREADS",
    "GUNICORN_TIMEOUT",
    "CELERY_CONCURRENCY",
}
CALLBACKS = {
    "CALL_EVENTS_URL": "call-events",
    "CALL_PROFILE_URL": "call-profile",
    "CALL_RECORDING_URL": "call-recording",
    "INBOUND_CONTEXT_URL": "inbound-context",
}


class ConfigError(Exception):
    """Contains field names and guidance, never credential values."""


def read_env(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("Environment must be a regular file, not a symlink.")
    values = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ConfigError(f"Invalid environment entry on line {number}.")
        if key in values:
            raise ConfigError(f"Duplicate environment setting: {key}.")
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
            if "'" in value:
                raise ConfigError(f"Embedded quotes are not supported in {key}.")
        elif value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
            if any(char in value for char in ("$", "\\", '"')):
                raise ConfigError(f"Use a single-quoted literal value for {key}.")
        elif any(char in value for char in ("$", "'", '"', "\\", "#")):
            raise ConfigError(f"Use a single-quoted literal value for {key}.")
        if any(ord(char) < 32 for char in value):
            raise ConfigError(f"Invalid control character in {key}.")
        values[key] = value
    return values


def secure_file(path: Path) -> None:
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ConfigError(
            "Environment file is readable by other users. Run chmod 600 on it."
        )


def domain_name(value: str) -> str:
    if (
        len(value) > 253
        or any(len(label) > 63 for label in value.split("."))
        or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+",
            value,
        )
    ):
        raise ConfigError(
            "SITE_DOMAIN must be a lowercase DNS hostname without a scheme or path."
        )
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if not value.endswith((".localhost", ".local", ".invalid")):
            return value
    raise ConfigError("SITE_DOMAIN must be a public DNS hostname.")


def integer(values, key, default, low, high):
    value = values.get(key, str(default))
    if not value.isascii() or not value.isdigit() or not low <= int(value) <= high:
        raise ConfigError(f"{key} must be a whole number from {low} to {high}.")
    return int(value)


def validate(values: dict[str, str]) -> None:
    domain = domain_name(values.get("SITE_DOMAIN", ""))
    integer(values, "APP_PORT", 8003, 1024, 65535)
    for key, default in (
        ("COMPOSE_PROJECT_NAME", "voice-agent"),
        ("POSTGRES_DB", "vaani"),
        ("POSTGRES_USER", "vaani"),
    ):
        pattern = (
            r"[a-z][a-z0-9_-]{0,39}"
            if key == "COMPOSE_PROJECT_NAME"
            else r"[a-z][a-z0-9_]{0,39}"
        )
        if not re.fullmatch(pattern, values.get(key, default)):
            raise ConfigError(f"{key} must be a simple lowercase identifier.")
    for key in KEY_NAMES:
        if len(values.get(key, "")) < 32:
            raise ConfigError(
                f"{key} is missing or too short. Set a secure value; preserve existing encryption keys."
            )
    for key in ("POSTGRES_PASSWORD", "REDIS_PASSWORD"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", values[key]):
            raise ConfigError(
                f"{key} must contain at least 32 URL-safe letters, digits, underscores or hyphens."
            )
    for key in ("CONTACT_ENCRYPTION_KEYS", "RECORDING_ENCRYPTION_KEYS"):
        for value in values[key].split(","):
            try:
                decoded = base64.b64decode(value.strip(), altchars=b"-_", validate=True)
            except ValueError:
                raise ConfigError(f"{key} must contain valid Fernet keys.") from None
            if len(decoded) != 32:
                raise ConfigError(f"{key} must contain valid Fernet keys.")
    for key, expected in {
        "DJANGO_DEBUG": "false",
        "DJANGO_BEHIND_PROXY": "true",
        "DJANGO_SSL_REDIRECT": "true",
        "DJANGO_SECURE_COOKIES": "true",
        "INTEGRATIONS_ALLOW_LOCAL": "false",
        "CELERY_TASK_ALWAYS_EAGER": "false",
    }.items():
        if values.get(key) != expected:
            raise ConfigError(
                f"{key} must be {expected} in this production deployment."
            )
    if values.get("DEV_AUTO_LOGIN_USER"):
        raise ConfigError("Remove DEV_AUTO_LOGIN_USER in production.")
    if values.get("DJANGO_ALLOWED_HOSTS") != domain:
        raise ConfigError(
            "DJANGO_ALLOWED_HOSTS must equal SITE_DOMAIN; wildcards are not accepted."
        )
    if values.get("DJANGO_CSRF_TRUSTED_ORIGINS") != f"https://{domain}":
        raise ConfigError(
            "DJANGO_CSRF_TRUSTED_ORIGINS must be https:// followed by SITE_DOMAIN."
        )
    if (
        values.get("RECORDING_STORAGE") != "local"
        or values.get("RECORDING_LOCAL_DIR") != "/app/private_recordings"
    ):
        raise ConfigError(
            "This shared-VPS stack requires local recording storage at /app/private_recordings."
        )
    for key, route in CALLBACKS.items():
        if values.get(key) != f"https://{domain}/api/telephony/{route}/":
            raise ConfigError(
                f"{key} must use SITE_DOMAIN and its /api/telephony/{route}/ endpoint."
            )
    for key, default, low, high in (
        ("GUNICORN_WORKERS", 1, 1, 4),
        ("GUNICORN_THREADS", 2, 1, 8),
        ("GUNICORN_TIMEOUT", 90, 30, 300),
        ("CELERY_CONCURRENCY", 1, 1, 4),
        ("BACKUP_KEEP_DAYS", 14, 1, 3650),
        ("DEPLOY_MIN_AVAILABLE_MB", 1200, 256, 1048576),
        ("DEPLOY_MIN_FREE_GB", 6, 2, 10000),
        ("DEPLOY_MIN_BUILD_GB", 3, 1, 10000),
        ("DEPLOY_MAX_SWAP_PERCENT", 80, 1, 95),
    ):
        integer(values, key, default, low, high)
    if values.get("BACKUP_DIR", "backups") != "backups":
        raise ConfigError(
            "BACKUP_DIR must be backups; scripts isolate backups by project."
        )
    if values.get("DEPLOY_PUBLIC_HTTPS", "false") not in ("true", "false"):
        raise ConfigError("DEPLOY_PUBLIC_HTTPS must be true or false.")
    if values.get("OUTBOUND_DIALING_MODE", "allowlist") not in ("allowlist", "live"):
        raise ConfigError("OUTBOUND_DIALING_MODE is invalid.")
    for key in ("LLM_BASE_URL", "LIVEKIT_INFERENCE_URL"):
        validate_provider_url(values, key, "https")
    validate_provider_url(values, "LIVEKIT_URL", "wss")


def validate_provider_url(values, key, scheme):
    if not values.get(key):
        return
    try:
        parsed = urlsplit(values[key])
        if (
            parsed.scheme == scheme
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        ):
            # Accessing port also validates malformed numeric ports without printing credentials.
            _ = parsed.port
            return
    except ValueError:
        pass
    raise ConfigError(
        f"{key} must be a valid {scheme}:// URL without embedded credentials."
    )


def write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def initialise(path, domain, port, project):
    if path.exists() or path.is_symlink():
        values = read_env(path)
        secure_file(path)
        validate(values)
        print("Existing environment validated; no values changed.")
        return
    template_path = ROOT / "deploy/production.env.example"
    template = template_path.read_text(encoding="utf-8")
    values = read_env(template_path)
    values.update(
        SITE_DOMAIN=domain_name(domain),
        APP_PORT=str(port),
        COMPOSE_PROJECT_NAME=project,
    )
    values["DJANGO_ALLOWED_HOSTS"] = domain
    values["DJANGO_CSRF_TRUSTED_ORIGINS"] = f"https://{domain}"
    for key in KEY_NAMES:
        values[key] = (
            base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
            if key.endswith("ENCRYPTION_KEYS")
            else secrets.token_hex(32)
        )
    for key, route in CALLBACKS.items():
        values[key] = f"https://{domain}/api/telephony/{route}/"
    validate(values)
    lines = []
    for line in template.splitlines():
        key = line.split("=", 1)[0]
        lines.append(f"{key}={values[key]}" if key in values else line)
    write_new(path, "\n".join(lines) + "\n")
    print(
        "Created a private production environment with new keys. No provider was activated."
    )
    print(
        "For an existing database, restore its original contact/recording keys BEFORE importing data."
    )


def worker_environment(values, output):
    # LiveKit Cloud supplies LIVEKIT_URL/API_KEY/API_SECRET for the selected project.
    allowed = {
        "AGENT_NAME",
        "SARVAM_API_KEY",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "LIVEKIT_INFERENCE_URL",
        "CALL_EVENTS_SECRET",
        "CALL_EVENTS_URL",
        "CALL_PROFILE_URL",
        "CALL_RECORDING_URL",
        "INBOUND_CONTEXT_URL",
        "AGENT_PROFILE_BUDGET_BYTES",
        "OUTBOUND_DIALING_MODE",
        "OUTBOUND_ALLOWLIST_NUMBERS",
        "OUTBOUND_TEST_ALLOWED_NUMBER",
    }
    defaults = read_env(ROOT / ".env.example")
    allowed.update(
        key
        for key in defaults
        if key.startswith(("AGENT_", "SARVAM_"))
        or key in ("NOISE_CANCELLATION", "BUSINESS_NAME", "INBOUND_UNROUTED_MESSAGE")
    )
    lines = [
        "# Private LiveKit worker secrets. Review against EXISTING worker tuning before deployment.",
        "# No database, contact/recording encryption or dashboard assistant keys are included.",
    ]
    for key in sorted(allowed):
        # Existing voice tuning is not replaced by example defaults on export.
        value = values.get(key, "")
        if value:
            if "'" in value:
                raise ConfigError(
                    f"Cannot export {key} with an embedded quote; set it privately in the worker file."
                )
            lines.append(f"{key}='{value}'")
    write_new(output, "\n".join(lines) + "\n")
    print(
        "Created a separate private worker file. Review current worker tuning before any approved deployment."
    )


def nginx_config(values, output, certificate, http_only):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,150}", certificate):
        raise ConfigError(
            "Certificate name must be a simple Certbot certificate directory name."
        )
    name = "http.conf.template" if http_only else "https.conf.template"
    text = (ROOT / "deploy/nginx" / name).read_text(encoding="utf-8")
    for key, value in {
        "DOMAIN": values["SITE_DOMAIN"],
        "PORT": values.get("APP_PORT", "8003"),
        "PROJECT": values.get("COMPOSE_PROJECT_NAME", "voice-agent"),
        "CERTIFICATE": certificate,
    }.items():
        text = text.replace("@@" + key + "@@", value)
    write_new(output, text)
    print(
        "Generated a Voice Agent Nginx candidate; host configuration has not changed."
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("init", "validate", "get", "worker-env", "nginx")
    )
    parser.add_argument("key", nargs="?")
    parser.add_argument(
        "--env-file", type=Path, default=ROOT / "deploy/.env.production"
    )
    parser.add_argument("--domain", default=DEFAULT_DOMAIN)
    parser.add_argument("--port", type=int, default=8003)
    parser.add_argument("--project", default="voice-agent")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--certificate-name")
    parser.add_argument("--http-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            initialise(args.env_file, args.domain, args.port, args.project)
            return 0
        values = read_env(args.env_file)
        secure_file(args.env_file)
        if args.command == "get":
            if args.key not in PUBLIC_NAMES:
                raise ConfigError(
                    "Only explicitly allowed non-secret deployment settings can be printed."
                )
            print(values.get(args.key, ""))
            return 0
        validate(values)
        if args.command == "validate":
            print("Production environment validated. Credentials are not displayed.")
        elif args.command == "worker-env":
            worker_environment(
                values, args.output or ROOT / "deploy/.env.worker.production"
            )
        elif args.command == "nginx":
            if not args.output:
                raise ConfigError("nginx requires --output for a new candidate file.")
            nginx_config(
                values,
                args.output,
                args.certificate_name or values["SITE_DOMAIN"],
                args.http_only,
            )
    except (ConfigError, FileExistsError) as error:
        message = (
            str(error)
            if isinstance(error, ConfigError)
            else "Output already exists; refusing to overwrite it."
        )
        print(f"Configuration error: {message}", file=sys.stderr)
        return 2
    except OSError:
        print(
            "Configuration error: cannot read/write the private file; check its path and permissions.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
