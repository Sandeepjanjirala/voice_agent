"""Execute real Bash scripts against fake process boundaries, never providers/VPS.

These tests verify control flow and fail-closed behavior. They do not replace
the documented isolated Docker acceptance test or production host verification.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
if os.name == "nt":
    candidate = Path("C:/Program Files/Git/bin/bash.exe")
    BASH = str(candidate) if candidate.exists() else None

pytestmark = pytest.mark.skipif(BASH is None, reason="Bash is required")

BOUNDARIES = r"""
need() { :; }
config() {
    if [[ "$1" == get ]]; then
        case "${*: -1}" in
            COMPOSE_PROJECT_NAME) printf 'voice-agent\n' ;;
            SITE_DOMAIN) printf 'voice.example.test\n' ;;
            APP_PORT) printf '8003\n' ;;
            POSTGRES_USER|POSTGRES_DB) printf 'vaani\n' ;;
            BACKUP_KEEP_DAYS) printf '14\n' ;;
            DEPLOY_MIN_AVAILABLE_MB) printf '1200\n' ;;
            DEPLOY_MIN_FREE_GB) printf '6\n' ;;
            DEPLOY_MIN_BUILD_GB) printf '3\n' ;;
            DEPLOY_MAX_SWAP_PERCENT) printf '80\n' ;;
            DEPLOY_PUBLIC_HTTPS) printf 'false\n' ;;
            *) return 1 ;;
        esac
    elif [[ "$1" == validate ]]; then
        [[ "${FAIL_ENV:-0}" == 0 ]] || return 24
    fi
}
lock_deployment() {
    mkdir -p "$STATE_DIR"
    if [[ -n "${PROJECT:-}" ]]; then validate_owned_resources; fi
}
git_repo() {
    printf 'git:%s\n' "$*" >> "$TRACE"
    case "$*" in
        'status --porcelain') [[ "${DIRTY:-0}" == 0 ]] || printf ' M file\n' ;;
        'rev-parse HEAD') printf 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n' ;;
        "rev-parse --verify FETCH_HEAD^{commit}") printf 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n' ;;
    esac
    return 0
}
docker() {
    printf 'docker:%s\n' "$*" >> "$TRACE"
    case "$*" in
        'inspect --format {{.Image}} postgres-id') printf 'postgres-test-image\n' ;;
        'container inspect --format '*|'volume inspect --format '*) printf '%s\n' "${*: -1}" ;;
        'container inspect '*|'volume inspect '*) [[ "${EXISTING_SCRATCH:-0}" == 1 ]] ;;
        'inspect --format {{.State.Status}} web-id') printf '%s\n' "${APP_STATUS:-running}" ;;
        'inspect --format {{.State.Status}} '*) printf 'running %s restarts=0\n' "${CONTAINER_HEALTH:-healthy}" ;;
        'inspect --format {{range '*) printf '127.0.0.1:8003\n' ;;
        'info --format {{.DockerRootDir}}') printf '%s\n' "$ROOT" ;;
        'ps -q --filter publish='*) [[ "${OWNED_PORT:-0}" == 0 ]] || printf 'web-id\n' ;;
        'image ls '*) printf 'voice-agent-web:v-abandoned\n' ;;
        'ps -aq --filter label=com.docker.compose.project='*)
            [[ "${FAIL_OWNERSHIP_QUERY:-0}" == 0 ]] || return 26
            [[ "${FOREIGN_PROJECT:-0}" == 0 ]] || printf 'foreign-container\n' ;;
        'inspect --format {{index .Config.Labels '*) printf '/srv/another-application\n' ;;
        'ps -aq '*) return 0 ;;
        *' pg_restore --exit-on-error --no-owner --no-privileges -U postgres -d restore_test')
            count="$(command grep -c '^docker:exec -i .* pg_restore ' "$TRACE")"
            [[ "$count" != "${FAIL_RESTORE_TEST_AT:-0}" ]] || return 30 ;;
    esac
}
dc() {
    printf 'dc:%s\n' "$*" >> "$TRACE"
    case "$*" in
        'exec -T postgres pg_dump '*)
            [[ "${FAIL_DUMP:-0}" == 0 ]] || return 15
            printf 'synthetic dump\n' ;;
        'exec -T postgres pg_restore --list')
            [[ "${FAIL_ARCHIVE:-0}" == 0 ]] || return 16
            cat >/dev/null ;;
        'exec -T postgres createdb '*) [[ "${FAIL_CREATE_DB:-0}" == 0 ]] || return 28 ;;
        'exec -T postgres pg_restore -U '*) [[ "${FAIL_PRODUCTION_RESTORE:-0}" == 0 ]] || return 29 ;;
        'ps --all -q postgres') printf 'postgres-id\n' ;;
        'ps --all -q web worker beat') [[ "${EXISTING_APP:-0}" == 0 ]] || printf 'web-id\n' ;;
        'ps --all -q '*) printf '%s-id\n' "${*: -1}" ;;
        'ps -q') printf 'web-id\nworker-id\nbeat-id\npostgres-id\nredis-id\n' ;;
        'exec -T web python manage.py deploycheck --check inflight --require-idle')
            count="$(awk '/^dc:exec -T web python manage.py deploycheck --check inflight --require-idle$/ {n++} END {print n+0}' "$TRACE")"
            [[ "$count" != "${FAIL_IDLE_AT:-0}" ]] || return 20 ;;
        *'--offline-workers') [[ "${FAIL_DRAIN:-0}" == 0 ]] || return 21 ;;
        'run --rm --no-deps migrate') [[ "${FAIL_MIGRATE:-0}" == 0 ]] || return 22 ;;
        'build web') [[ "${FAIL_BUILD:-0}" == 0 ]] || return 23 ;;
    esac
    return 0
}
wait_healthy() {
    printf 'healthy:%s\n' "$1" >> "$TRACE"
    [[ "${FAIL_DATABASE:-0}" == 0 ]] || die 'unhealthy database'
}
start_backend() { printf 'start_backend\n' >> "$TRACE"; }
stat() { case "$*" in '-c %a '*) printf '600\n' ;; '-c %u '*) id -u ;; *) command stat "$@" ;; esac; }
getent() { [[ "${FAIL_DNS:-0}" == 0 ]]; }
getconf() { printf '1\n'; }
df() { printf 'Filesystem 1024-blocks Used Available Capacity Mounted\n'; printf '/dev/mock 50000000 10000000 %s 20%% /\n' "${MOCK_DISK_KB:-30000000}"; }
free() { :; }
ss() { [[ "${PORT_CONFLICT:-0}" == 0 ]] || printf 'LISTEN 0 100 127.0.0.1:8003\n'; }
awk() {
    if [[ "${*: -1}" == /proc/meminfo ]]; then
        case "$1" in *MemAvailable*) printf '%s\n' "${MOCK_RAM_MB:-2000}" ;; *SwapTotal*) printf '%s\n' "${MOCK_SWAP_PERCENT:-0}" ;; esac
    else command awk "$@"; fi
}
curl() {
    [[ "${FAIL_HTTP:-0}" == 0 ]] || return 22
    local healthy='{"status":"ok","service":"django","database":"ok"}'
    case "${*: -1}" in
        */api/health/) printf '%s\n%s' "${MOCK_HEALTH_BODY:-$healthy}" "${MOCK_HTTP_STATUS:-200}" ;;
        */static/admin/css/base.css) printf '%s %s' "${MOCK_STATIC_STATUS:-200}" "${MOCK_STATIC_TYPE:-text/css}" ;;
        */api/health/ready/) printf '404' ;;
    esac
}
"""


@pytest.fixture
def checkout(tmp_path):
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "deploy/.state").mkdir(parents=True)
    (tmp_path / "deploy/.env.production").write_text("SECRET=preserved\n")
    helper = tmp_path / "scripts/lib/deployment.sh"
    helper.write_text(helper.read_text() + BOUNDARIES)
    for name in ("preflight", "healthcheck"):
        shutil.copyfile(
            tmp_path / f"scripts/{name}.sh",
            tmp_path / f"scripts/{name}.actual.sh",
        )
        (tmp_path / f"scripts/{name}.sh").write_text(
            f'#!/usr/bin/env bash\nprintf "{name}\\n" >> "$TRACE"\n'
        )
    (tmp_path / "trace").touch()
    return tmp_path


def current(checkout):
    (checkout / "deploy/.state/current").write_text(
        "voice-agent-web\nv-old\n" + "a" * 40 + "\n"
    )


def run_script(checkout, name, *args, **flags):
    env = os.environ.copy()
    for key in ("APP_IMAGE", "APP_IMAGE_TAG", "VAANI_ENV_FILE", "VAANI_LOCK_HELD"):
        env.pop(key, None)
    env.update(TRACE=str(checkout / "trace"), **{k: str(v) for k, v in flags.items()})
    return subprocess.run(
        [BASH, str(checkout / f"scripts/{name}.sh"), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def trace(checkout):
    return (checkout / "trace").read_text()


def test_bash_syntax():
    paths = [*ROOT.glob("scripts/*.sh"), *ROOT.glob("scripts/lib/*.sh")]
    paths.append(ROOT / "deploy/backup.sh")
    for path in paths:
        result = subprocess.run([BASH, "-n", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, f"{path}: {result.stderr}"


def test_fresh_deploy_migrates_before_backend(checkout):
    result = run_script(checkout, "deploy")
    assert result.returncode == 0, result.stderr
    calls = trace(checkout)
    assert calls.index("healthy:postgres") < calls.index(
        "dc:run --rm --no-deps migrate"
    )
    assert calls.index("dc:run --rm --no-deps migrate") < calls.index("start_backend")
    assert "exec -T web python manage.py migrate" not in calls
    assert (checkout / "deploy/.env.production").read_text() == "SECRET=preserved\n"
    assert "v-aaaaaaaaaaaa-" in (checkout / "deploy/.state/current").read_text()


def test_repeated_deploy_preserves_image_environment_and_keys(checkout):
    current(checkout)
    before = (checkout / "deploy/.state/current").read_text()
    result = run_script(checkout, "deploy")
    assert result.returncode == 0, result.stderr
    assert "dc:build" not in trace(checkout)
    assert "dc:run --rm --no-deps migrate" not in trace(checkout)
    assert (checkout / "deploy/.state/current").read_text() == before
    assert (checkout / "deploy/.env.production").read_text() == "SECRET=preserved\n"


@pytest.mark.parametrize("flag", ["FAIL_MIGRATE", "FAIL_DATABASE"])
def test_failed_initial_database_or_migration_never_starts_backend(checkout, flag):
    result = run_script(checkout, "deploy", **{flag: 1})
    assert result.returncode != 0
    assert "start_backend" not in trace(checkout)
    assert not (checkout / "deploy/.state/current").exists()


@pytest.mark.parametrize("flag", ["FAIL_DUMP", "FAIL_ARCHIVE"])
def test_bad_backup_never_becomes_a_completed_dump(checkout, flag):
    result = run_script(checkout, "backup", **{flag: 1})
    assert result.returncode != 0
    assert not list((checkout / "backups").rglob("*.dump"))


@pytest.mark.parametrize("flag", ["FAIL_DUMP", "FAIL_ARCHIVE"])
def test_update_backup_failure_prevents_fetch_migration_and_shutdown(checkout, flag):
    current(checkout)
    result = run_script(checkout, "update", **{flag: 1})
    assert result.returncode != 0
    calls = trace(checkout)
    assert "git:fetch" not in calls
    assert "dc:stop" not in calls
    assert "dc:run --rm --no-deps migrate" not in calls
    assert not list((checkout / "backups").rglob("*.dump"))


def test_explicit_database_restore_recreates_empty_database_while_backend_stopped(
    checkout,
):
    dump = checkout / "restore.dump"
    dump.write_text("synthetic archive")
    result = run_script(
        checkout,
        "restore",
        str(dump),
        "--replace-production",
        "--confirm-project",
        "voice-agent",
    )
    assert result.returncode == 0, result.stderr
    calls = trace(checkout)
    assert calls.index("--offline-workers") < calls.index("pg_dump")
    assert calls.index("pg_dump") < calls.index(
        "dropdb -U vaani --force --if-exists -- vaani"
    )
    assert calls.index("dropdb -U vaani --force --if-exists -- vaani") < calls.index(
        "createdb -U vaani --owner vaani -- vaani"
    )
    assert "start_backend" not in calls
    assert "dc:up" not in calls
    assert not (checkout / "deploy/.state/pending-restore").exists()


@pytest.mark.parametrize("flags", [{"DIRTY": 1}, {"FAIL_IDLE_AT": 1}])
def test_update_stops_before_backup_fetch_and_shutdown(checkout, flags):
    current(checkout)
    result = run_script(checkout, "update", **flags)
    assert result.returncode != 0
    calls = trace(checkout)
    assert "pg_dump" not in calls
    assert "git:fetch" not in calls
    assert "dc:stop" not in calls


def test_update_order_and_networkless_restore(checkout):
    current(checkout)
    result = run_script(checkout, "update")
    assert result.returncode == 0, result.stderr
    calls = trace(checkout)
    assert calls.index("pg_dump") < calls.index("git:fetch")
    assert calls.index("git:fetch") < calls.index("dc:build web")
    assert calls.index("dc:build web") < calls.index("dc:stop -t 30 beat web")
    assert calls.index("--offline-workers") < calls.index(
        "dc:run --rm --no-deps migrate"
    )
    assert calls.index("dc:run --rm --no-deps migrate") < calls.index("start_backend")
    assert "--network none" in calls
    assert "--mount type=volume,source=voice-agent-restore-test-" in calls
    assert "-e POSTGRES_HOST_AUTH_METHOD=trust" in calls
    assert "docker:container rm -f voice-agent-restore-test-" in calls
    assert (checkout / "deploy/.state/previous").read_text().splitlines()[1] == "v-old"
    assert not (checkout / "deploy/.state/pending-update").exists()


def test_failed_migration_preserves_state_and_stops_backend(checkout):
    current(checkout)
    result = run_script(checkout, "update", FAIL_MIGRATE=1)
    assert result.returncode != 0
    assert "start_backend" not in trace(checkout)
    assert (checkout / "deploy/.state/pending-update").exists()
    assert (checkout / "deploy/.state/current").read_text().splitlines()[1] == "v-old"


def test_call_arriving_during_build_cancels_without_shutdown(checkout):
    current(checkout)
    result = run_script(checkout, "update", FAIL_IDLE_AT=2)
    assert result.returncode != 0
    assert "dc:build web" in trace(checkout)
    assert "dc:stop" not in trace(checkout)
    assert not (checkout / "deploy/.state/pending-update").exists()
    assert "git:checkout --detach " + "a" * 40 in trace(checkout)


def test_queued_work_after_drain_resumes_old_services(checkout):
    current(checkout)
    result = run_script(checkout, "update", FAIL_DRAIN=1)
    assert result.returncode != 0
    assert "dc:up -d --no-deps --no-build --scale beat=1 web worker beat" in trace(
        checkout
    )
    assert "dc:run --rm --no-deps migrate" not in trace(checkout)


def test_restore_defaults_to_isolated_test(checkout):
    dump = checkout / "restore.dump"
    dump.write_text("synthetic archive")
    result = run_script(checkout, "restore", str(dump))
    assert result.returncode == 0, result.stderr
    calls = trace(checkout)
    assert "--network none" in calls
    assert "dc:stop" not in calls
    assert "--clean --if-exists" not in calls


def test_cleanup_dry_run_never_deletes_images(checkout):
    result = run_script(checkout, "cleanup", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "voice-agent-web:v-abandoned" in result.stderr
    assert "docker:image rm" not in trace(checkout)


def test_cleanup_requires_exact_project_confirmation(checkout):
    result = run_script(checkout, "cleanup", "--apply", "--confirm-project", "edunexa")
    assert result.returncode != 0
    assert "docker:image rm" not in trace(checkout)


def test_rollback_requires_explicit_schema_review(checkout):
    result = run_script(checkout, "rollback")
    assert result.returncode != 0
    assert "dc:" not in trace(checkout)


def test_interrupted_update_blocks_redeployment(checkout):
    (checkout / "deploy/.state/pending-update").write_text("unfinished")
    result = run_script(checkout, "deploy")
    assert result.returncode != 0
    assert "start_backend" not in trace(checkout)


def test_preflight_accepts_safe_host_without_changing_services(checkout):
    result = run_script(checkout, "preflight.actual")
    assert result.returncode == 0, result.stderr
    assert "dc:up" not in trace(checkout)
    assert "dc:stop" not in trace(checkout)


@pytest.mark.parametrize(
    "flags,diagnostic",
    [
        ({"MOCK_DISK_KB": 1024}, "Insufficient disk space"),
        ({"MOCK_RAM_MB": 100}, "Insufficient available RAM"),
        ({"MOCK_SWAP_PERCENT": 90}, "Swap pressure"),
        ({"PORT_CONFLICT": 1}, "Port 8003 is used"),
        ({"FAIL_DNS": 1}, "DNS does not resolve"),
        ({"FAIL_ENV": 1}, "Stopped"),
        ({"FOREIGN_PROJECT": 1}, "belongs to another checkout"),
    ],
)
def test_preflight_fails_closed(checkout, flags, diagnostic):
    result = run_script(checkout, "preflight.actual", **flags)
    assert result.returncode != 0
    assert diagnostic in result.stderr
    assert "dc:up" not in trace(checkout)


def test_preflight_accepts_only_own_existing_loopback_port(checkout):
    result = run_script(checkout, "preflight.actual", PORT_CONFLICT=1, OWNED_PORT=1)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("state", ["starting", "unhealthy", "no-healthcheck"])
def test_health_fails_unready_containers(checkout, state):
    result = run_script(checkout, "healthcheck.actual", CONTAINER_HEALTH=state)
    assert result.returncode != 0
    assert "health checks failed" in result.stderr


def test_health_rejects_http_errors(checkout):
    result = run_script(checkout, "healthcheck.actual", FAIL_HTTP=1)
    assert result.returncode != 0


def test_health_no_provider_calls(checkout):
    result = run_script(checkout, "healthcheck.actual")
    assert result.returncode == 0, result.stderr
    assert "no calls placed" in result.stderr
    assert "livekit" not in trace(checkout).lower()


def test_first_deploy_rejects_untracked_existing_application_containers(checkout):
    result = run_script(checkout, "deploy", EXISTING_APP=1)
    assert result.returncode != 0
    assert "dc:run --rm --no-deps migrate" not in trace(checkout)


def test_first_deploy_failure_leaves_recovery_marker(checkout):
    result = run_script(checkout, "deploy", FAIL_MIGRATE=1)
    assert result.returncode != 0
    assert (checkout / "deploy/.state/pending-deploy").exists()


def test_two_backups_in_same_process_never_overwrite(checkout):
    current(checkout)
    result = run_script(checkout, "update")
    assert result.returncode == 0, result.stderr
    assert len(list((checkout / "backups").rglob("*.dump"))) == 2


def test_compose_clears_other_application_interpolation_environment(tmp_path):
    compose = tmp_path / "compose.yaml"
    compose.write_text(
        "port: ${APP_PORT}\npassword: ${POSTGRES_PASSWORD}\nimage: ${APP_IMAGE}:${APP_IMAGE_TAG}\n"
    )
    command = r"""
source "$1"
COMPOSE_FILE="$2"
PROJECT=voice-agent
ENV_FILE=not-read-by-fake-docker
export POSTGRES_PASSWORD=synthetic-other-app APP_PORT=8002 APP_IMAGE=voice-agent-web APP_IMAGE_TAG=v-test
docker() {
    [[ -z "${POSTGRES_PASSWORD+x}" && -z "${APP_PORT+x}" ]] || exit 41
    [[ "$APP_IMAGE:$APP_IMAGE_TAG" == voice-agent-web:v-test ]] || exit 42
}
dc config --quiet
"""
    result = subprocess.run(
        [
            BASH,
            "-c",
            command,
            "test",
            str(ROOT / "scripts/lib/deployment.sh"),
            str(compose),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_scratch_restore_never_reuses_or_deletes_existing_resources(checkout):
    dump = checkout / "restore.dump"
    dump.write_text("synthetic archive")
    result = run_script(checkout, "restore", str(dump), EXISTING_SCRATCH=1)
    assert result.returncode != 0
    calls = trace(checkout)
    assert "docker:volume create" not in calls
    assert "docker:container rm" not in calls
    assert "docker:volume rm" not in calls


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("backup", ()),
        ("backup", ("--recordings",)),
        ("restore", ("--replace-production", "--confirm-project", "voice-agent")),
        ("rollback", ("--confirm-schema-compatible",)),
        ("cleanup", ("--apply", "--confirm-project", "voice-agent")),
        ("manage", ("migrate",)),
        ("healthcheck.actual", ()),
    ],
)
def test_management_rejects_another_checkouts_project(checkout, name, arguments):
    current(checkout)
    if name == "restore":
        dump = checkout / "synthetic.dump"
        dump.write_text("synthetic archive")
        arguments = (str(dump), *arguments)
    result = run_script(checkout, name, *arguments, FOREIGN_PROJECT=1)
    assert result.returncode != 0
    assert "belongs to another checkout" in result.stderr
    # No Compose command may read/mutate the foreign installation, even when
    # the operator supplied this checkout's expected project confirmation.
    assert "dc:" not in trace(checkout)


@pytest.mark.parametrize("name", ["backup", "preflight.actual"])
def test_failed_ownership_inspection_is_not_an_empty_installation(checkout, name):
    result = run_script(checkout, name, FAIL_OWNERSHIP_QUERY=1)
    assert result.returncode != 0
    assert "postgres pg_dump" not in trace(checkout)
    assert "dc:stop" not in trace(checkout)


def test_real_lock_validates_ownership_after_acquiring_lock(tmp_path):
    command = r"""
source "$1"
ROOT="$2"
STATE_DIR="$ROOT/state"
PROJECT=voice-agent
need() { :; }
flock() { printf 'locked\n' > "$ROOT/order"; }
validate_owned_resources() {
    [[ "$(cat "$ROOT/order")" == locked ]] || exit 27
    printf 'validated\n' >> "$ROOT/order"
}
lock_deployment
"""
    result = subprocess.run(
        [
            BASH,
            "-c",
            command,
            "test",
            str(ROOT / "scripts/lib/deployment.sh"),
            tmp_path.as_posix(),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "order").read_text().splitlines() == ["locked", "validated"]


@pytest.mark.parametrize("flag", ["FAIL_CREATE_DB", "FAIL_PRODUCTION_RESTORE"])
def test_failed_replacement_can_resume_without_querying_incomplete_schema(
    checkout, flag
):
    dump = checkout / "restore.dump"
    dump.write_text("synthetic archive")
    result = run_script(
        checkout,
        "restore",
        str(dump),
        "--replace-production",
        "--confirm-project",
        "voice-agent",
        **{flag: 1},
    )
    assert result.returncode != 0
    pending = checkout / "deploy/.state/pending-restore"
    recorded = pending.read_text().splitlines()
    assert recorded[:3] == ["voice-agent", "vaani", "vaani"]
    safety = next((checkout / "backups/voice-agent").glob("*.dump"))
    assert safety.is_file()
    (checkout / "trace").write_text("")
    result = run_script(
        checkout,
        "restore",
        recorded[3],
        "--resume-replacement",
        "--confirm-project",
        "voice-agent",
        FAIL_DRAIN=1,
        FAIL_DUMP=1,
    )
    assert result.returncode == 0, result.stderr
    calls = trace(checkout)
    assert "--offline-workers" not in calls
    assert "pg_dump" not in calls
    assert "dropdb -U vaani --force --if-exists -- vaani" in calls
    assert "dc:up" not in calls and "dc:stop" not in calls
    assert not pending.exists()
    assert safety.is_file()


def interrupted_restore(checkout):
    dump = checkout / "restore.dump"
    dump.write_text("synthetic archive")
    result = run_script(
        checkout,
        "restore",
        str(dump),
        "--replace-production",
        "--confirm-project",
        "voice-agent",
        FAIL_PRODUCTION_RESTORE=1,
    )
    assert result.returncode != 0
    (checkout / "trace").write_text("")
    return dump


@pytest.mark.parametrize("status", ["running", "restarting", "paused"])
def test_replacement_resume_refuses_any_active_application_container(checkout, status):
    dump = interrupted_restore(checkout)
    result = run_script(
        checkout,
        "restore",
        str(dump),
        "--resume-replacement",
        "--confirm-project",
        "voice-agent",
        EXISTING_APP=1,
        APP_STATUS=status,
    )
    assert result.returncode != 0
    assert "already stopped" in result.stderr
    assert "dropdb" not in trace(checkout)
    assert "dc:stop" not in trace(checkout)


@pytest.mark.parametrize("tamper", ["source", "safety", "project", "unrelated"])
def test_replacement_resume_requires_recorded_unchanged_archives(checkout, tamper):
    dump = interrupted_restore(checkout)
    confirmation = "voice-agent"
    if tamper == "source":
        dump.write_text("changed source")
    elif tamper == "safety":
        next((checkout / "backups/voice-agent").glob("*.dump")).write_text(
            "changed safety"
        )
    elif tamper == "project":
        confirmation = "other-project"
    else:
        dump = checkout / "unrelated.dump"
        dump.write_text("synthetic archive")
    result = run_script(
        checkout,
        "restore",
        str(dump),
        "--resume-replacement",
        "--confirm-project",
        confirmation,
    )
    assert result.returncode != 0
    assert "dropdb" not in trace(checkout)
    assert (checkout / "deploy/.state/pending-restore").exists()


def test_replacement_resume_requires_existing_pending_state(checkout):
    dump = checkout / "restore.dump"
    dump.write_text("synthetic archive")
    result = run_script(
        checkout,
        "restore",
        str(dump),
        "--resume-replacement",
        "--confirm-project",
        "voice-agent",
    )
    assert result.returncode != 0
    assert "dropdb" not in trace(checkout)


def test_failed_safety_backup_restore_drill_prevents_database_drop(checkout):
    dump = checkout / "restore.dump"
    dump.write_text("synthetic archive")
    result = run_script(
        checkout,
        "restore",
        str(dump),
        "--replace-production",
        "--confirm-project",
        "voice-agent",
        FAIL_RESTORE_TEST_AT=2,
    )
    assert result.returncode != 0
    assert "dropdb" not in trace(checkout)
    assert not (checkout / "deploy/.state/pending-restore").exists()


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("deploy", ()),
        ("update", ()),
        ("rollback", ("--confirm-schema-compatible",)),
        ("manage", ("migrate",)),
    ],
)
def test_pending_restore_blocks_ordinary_management_startup(checkout, name, arguments):
    (checkout / "deploy/.state/pending-restore").write_text("interrupted")
    result = run_script(checkout, name, *arguments)
    assert result.returncode != 0
    assert "replacement is incomplete" in result.stderr.lower()
    assert "dc:" not in trace(checkout)


@pytest.mark.parametrize(
    "flags",
    [
        {"MOCK_HTTP_STATUS": 301},
        {"MOCK_HTTP_STATUS": 204},
        {"MOCK_HEALTH_BODY": "unrelated application"},
        {"MOCK_STATIC_STATUS": 302},
        {"MOCK_STATIC_TYPE": "text/html"},
    ],
)
def test_health_requires_expected_200_json_and_css(checkout, flags):
    result = run_script(checkout, "healthcheck.actual", **flags)
    assert result.returncode != 0
