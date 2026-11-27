#!/usr/bin/env bash
# Shared Ubuntu deployment primitives. Never source/evaluate an env file.
set -Eeuo pipefail
shopt -s inherit_errexit
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$ROOT"
ENV_FILE="${VAANI_ENV_FILE:-$ROOT/deploy/.env.production}"
COMPOSE_FILE="$ROOT/deploy/compose.shared.yaml"
STATE_DIR="$ROOT/deploy/.state"
BOOTSTRAP_IMAGE="${VAANI_BOOTSTRAP_IMAGE:-python:3.12-slim-bookworm}"
log() { printf '[voice-agent] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }
http_health() {
    local url="$1" response status body
    shift
    response="$(curl --silent --show-error --max-time 30 --max-filesize 4096 \
        -H 'Accept: application/json' --write-out $'\n%{http_code}' "$@" "$url")" || return 1
    status="${response##*$'\n'}"
    body="${response%$'\n'*}"
    [[ "$status" == 200 && "$body" == '{"status":"ok","service":"django","database":"ok"}' ]]
}
need() { command -v "$1" >/dev/null 2>&1 || die "Required command '$1' is missing. Install it separately; shared host services are never installed automatically."; }
on_error() {
    local result=$?
    log "Stopped (exit $result) in ${0##*/}. Data volumes and secrets are preserved. Inspect service logs privately; do not paste environment/config output. See docs/VPS_DEPLOYMENT.md."
    exit "$result"
}
trap on_error ERR
assert_managed_path() {
    local path="$1" cursor="$ROOT" component
    [[ "$path" == "$ROOT/"* ]] || die "Managed files must stay inside this checkout."
    local parts=()
    IFS=/ read -r -a parts <<< "${path#"$ROOT"/}"
    for component in "${parts[@]}"; do
        [[ -n "$component" && "$component" != . && "$component" != .. ]] || die "Invalid managed path."
        cursor="$cursor/$component"
        [[ ! -L "$cursor" ]] || die "Refusing a symbolic link in managed environment/state/backup paths."
    done
}
config() {
    docker run --rm --network none --user "$(id -u):$(id -g)" \
        -v "$ROOT:/workspace" -w /workspace "$BOOTSTRAP_IMAGE" \
        python scripts/configure.py "$@"
}
env_relative() {
    assert_managed_path "$ENV_FILE"
    case "$ENV_FILE" in "$ROOT"/*) printf '%s\n' "${ENV_FILE#"$ROOT"/}" ;; *) die "VAANI_ENV_FILE must be inside this checkout." ;; esac
}
config_get() { config get --env-file "$(env_relative)" "$1"; }
load_runtime() {
    need docker
    [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || die "Missing regular env file: $ENV_FILE. Run bash scripts/deploy.sh to initialize it."
    PROJECT="$(config_get COMPOSE_PROJECT_NAME)"
    DOMAIN="$(config_get SITE_DOMAIN)"
    PORT="$(config_get APP_PORT)"
    DB_USER="$(config_get POSTGRES_USER)"
    DB_NAME="$(config_get POSTGRES_DB)"
    [[ "$PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "Invalid Compose project name."
    [[ "$PORT" =~ ^[0-9]+$ && "$PORT" -gt 1024 && "$PORT" -lt 65536 ]] || die "Invalid application port."
    [[ "$DOMAIN" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]+$ ]] || die "Invalid site domain."
    [[ "$DB_USER" =~ ^[a-z_][a-z0-9_]*$ && "$DB_NAME" =~ ^[a-z_][a-z0-9_]*$ ]] || die "Invalid PostgreSQL user/database name."
    BACKUP_ROOT="$ROOT/backups/$PROJECT"
    assert_managed_path "$STATE_DIR"
    assert_managed_path "$BACKUP_ROOT"
    export APP_ENV_FILE="$ENV_FILE" COMPOSE_PROJECT_NAME="$PROJECT"
    if [[ -z "${APP_IMAGE_TAG:-}" && -f "$STATE_DIR/current" ]]; then APP_IMAGE_TAG="$(state_field current 2)"; fi
    if [[ -z "${APP_IMAGE_TAG:-}" && -f "$STATE_DIR/pending-deploy" ]]; then APP_IMAGE_TAG="$(state_field pending-deploy 2)"; fi
    if [[ -z "${APP_IMAGE:-}" && -f "$STATE_DIR/current" ]]; then APP_IMAGE="$(state_field current 1)"; fi
    export APP_IMAGE_TAG="${APP_IMAGE_TAG:-local}" APP_IMAGE="${APP_IMAGE:-voice-agent-web}"
    [[ "$APP_IMAGE" =~ ^[a-zA-Z0-9./_-]+$ && "$APP_IMAGE_TAG" =~ ^[a-zA-Z0-9_.-]+$ ]] || die "Unsafe application image reference."
}
dc() (
    # Compose normally gives exported shell variables precedence over --env-file.
    # Clear only variables referenced by this reviewed Compose file, preserving
    # Docker context/SSH credentials while avoiding another app's DB credentials.
    local name references
    # shellcheck disable=SC2016 # Match Compose substitutions literally.
    local interpolation_pattern='\${[A-Z_][A-Z0-9_]*'
    references="$(grep -o "$interpolation_pattern" "$COMPOSE_FILE" | sed 's/^${//' | sort -u)"
    while IFS= read -r name; do
        case "$name" in APP_IMAGE|APP_IMAGE_TAG|APP_ENV_FILE|COMPOSE_PROJECT_NAME) ;;
            *) unset "$name" ;;
        esac
    done <<< "$references"
    docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
)
lock_deployment() {
    need flock
    assert_managed_path "$STATE_DIR"
    mkdir -p "$STATE_DIR"
    chmod 700 "$STATE_DIR"
    if [[ "${VAANI_LOCK_HELD:-}" != "$ROOT" || "$(readlink "/proc/$$/fd/9" 2>/dev/null || true)" != "$STATE_DIR/operation.lock" ]]; then
        exec 9>"$STATE_DIR/operation.lock"
        flock -n 9 || { log "Another deployment, backup, restore, or cleanup is running."; exit 75; }
        export VAANI_LOCK_HELD="$ROOT"
    fi
    # Recovery and management commands must enforce the same ownership boundary
    # as deployment. A matching project name alone does not identify a checkout.
    # First install locks before loading PROJECT; its preflight performs the gate.
    if [[ -n "${PROJECT:-}" ]]; then validate_owned_resources; fi
}
git_repo() {
    local owner
    owner="$(stat -c %u "$ROOT")"
    if [[ "$(id -u)" == 0 && "$owner" != 0 ]]; then
        need runuser
        runuser -u "$(stat -c %U "$ROOT")" -- git -C "$ROOT" "$@"
    else git -C "$ROOT" "$@"; fi
}
state_field() {
    local path="$STATE_DIR/$1"
    [[ -f "$path" && ! -L "$path" ]] || return 1
    sed -n "${2}p" "$path"
}
record_current() {
    local revision="$1"
    mkdir -p "$STATE_DIR"
    [[ ! -f "$STATE_DIR/current" ]] || cp "$STATE_DIR/current" "$STATE_DIR/previous"
    printf '%s\n%s\n%s\n' "$APP_IMAGE" "$APP_IMAGE_TAG" "$revision" > "$STATE_DIR/current.new"
    mv "$STATE_DIR/current.new" "$STATE_DIR/current"
    chmod 600 "$STATE_DIR/current"
}
container_id() { dc ps --all -q "$1"; }
wait_healthy() {
    local service="$1" deadline=$((SECONDS + ${2:-180})) id status
    while (( SECONDS < deadline )); do
        id="$(container_id "$service")"
        if [[ -n "$id" ]]; then
            status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id")"
            [[ "$status" == healthy ]] && return 0
            [[ "$status" != exited && "$status" != dead ]] || die "$service stopped. Inspect its logs before retrying."
        fi
        sleep 3
    done
    die "$service did not become healthy. Inspect its logs; no further startup will be attempted."
}
guard_idle() { dc exec -T web python manage.py deploycheck --check inflight --require-idle; }
stop_admission_and_drain() {
    log "Pausing admission and schedules, then allowing jobs to finish. This is a maintenance window."
    dc stop -t 30 beat web
    dc stop -t 90 worker
    if ! dc run --rm --no-deps --entrypoint python web manage.py deploycheck --check inflight --require-idle --offline-workers; then
        dc up -d --no-deps --no-build --scale beat=1 web worker beat
        die "Work appeared while draining; old services resumed. Wait for calls/jobs to finish."
    fi
}
drain_existing_backend() {
    export RESUME_BACKEND=false
    if [[ -n "$(dc ps --status running -q web)" ]]; then
        guard_idle
        stop_admission_and_drain
        RESUME_BACKEND=true
    else
        # Failed migrations deliberately leave admission stopped. Recovery must
        # remain possible, while stopping any surviving scheduler/worker first.
        dc stop -t 30 beat
        dc stop -t 90 worker
        dc run --rm --no-deps --entrypoint python web manage.py deploycheck --check inflight --require-idle --offline-workers
    fi
}
start_backend() {
    [[ ! -e "$STATE_DIR/pending-restore" ]] || die "Database recovery is pending. Complete the explicit restore recovery before starting services."
    dc up -d --no-build --no-deps --scale beat=1 web worker beat
    wait_healthy web 180
    wait_healthy worker 180
    local deadline=$((SECONDS + 150))
    until dc exec -T web python manage.py deploycheck --check readiness --check worker --check beat --check storage; do
        (( SECONDS < deadline )) || die "Readiness checks failed. Image has not been marked successful."
        sleep 5
    done
    wait_healthy beat 150
}
build_application() {
    local revision="$1"
    APP_IMAGE_TAG="v-${revision:0:12}-$(date -u +%Y%m%dT%H%M%SZ)"
    export APP_IMAGE_TAG
    log "Building immutable application image $APP_IMAGE:$APP_IMAGE_TAG."
    dc build web
}
verify_backup() {
    local file="$1"
    [[ -f "$file" && ! -L "$file" && -s "$file" ]] || die "Backup is missing, empty, or a symbolic link."
    dc exec -T postgres pg_restore --list < "$file" > /dev/null
    if [[ -f "$file.sha256" ]]; then (cd "$(dirname "$file")" && sha256sum -c "$(basename "$file").sha256" >/dev/null); fi
}
backup_database() {
    local stamp file partial
    assert_managed_path "$BACKUP_ROOT"
    mkdir -p "$BACKUP_ROOT"
    chmod 700 "$BACKUP_ROOT"
    stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
    partial="$(mktemp "$BACKUP_ROOT/$PROJECT-$stamp-XXXXXX.dump.partial")"
    file="${partial%.partial}"
    log "Writing private PostgreSQL backup. Encryption keys are deliberately excluded."
    dc exec -T postgres pg_dump -U "$DB_USER" -d "$DB_NAME" --format=custom --no-owner > "$file.partial"
    verify_backup "$file.partial"
    mv "$file.partial" "$file"
    (cd "$BACKUP_ROOT" && sha256sum "$(basename "$file")" > "$(basename "$file").sha256")
    printf '%s\n' "$file"
}
restore_test() {
    local file="$1" postgres_id image test_name deadline
    verify_backup "$file"
    postgres_id="$(container_id postgres)"
    image="$(docker inspect --format '{{.Image}}' "$postgres_id")"
    test_name="$PROJECT-restore-test-$(date +%s)-$$-$RANDOM"
    log "Testing restore in a networkless disposable PostgreSQL container."
    (
        # A disk-backed, explicitly named scratch volume avoids consuming the
        # shared VPS RAM with database-sized tmpfs. Only this exact resource is
        # ever removed, and only after its restore-test ownership is verified.
        if docker container inspect "$test_name" >/dev/null 2>&1 || docker volume inspect "$test_name" >/dev/null 2>&1; then
            die "Scratch restore name already exists; no existing resource will be reused or removed."
        fi
        docker volume create --label io.vaani.application=voice-agent \
            --label "io.vaani.restore-test=$test_name" "$test_name" >/dev/null
        # shellcheck disable=SC2329 # Called by the EXIT trap in this subshell.
        cleanup_restore_test() {
            if [[ "$(docker container inspect --format '{{index .Config.Labels "io.vaani.restore-test"}}' "$test_name" 2>/dev/null)" == "$test_name" ]]; then
                docker container rm -f "$test_name" >/dev/null
            fi
            if [[ "$(docker volume inspect --format '{{index .Labels "io.vaani.restore-test"}}' "$test_name" 2>/dev/null)" == "$test_name" ]]; then
                docker volume rm "$test_name" >/dev/null
            fi
        }
        trap cleanup_restore_test EXIT
        [[ "$(docker volume inspect --format '{{index .Labels "io.vaani.restore-test"}}' "$test_name")" == "$test_name" ]] || die "Scratch volume ownership could not be verified."
        docker run -d --name "$test_name" --network none --memory 384m --cpus 0.5 \
            --label io.vaani.application=voice-agent --label "io.vaani.restore-test=$test_name" \
            --mount "type=volume,source=$test_name,target=/var/lib/postgresql/data" \
            -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=restore_test "$image" >/dev/null
        deadline=$((SECONDS + 120))
        until docker exec "$test_name" pg_isready -U postgres -d restore_test >/dev/null 2>&1; do
            (( SECONDS < deadline )) || die "Isolated restore database failed to start."
            sleep 2
        done
        docker exec -i "$test_name" pg_restore --exit-on-error --no-owner --no-privileges -U postgres -d restore_test < "$file"
        docker exec "$test_name" psql -X -v ON_ERROR_STOP=1 -U postgres -d restore_test -Atc 'SELECT count(*) FROM django_migrations' >/dev/null
    )
    log "Isolated restore passed; temporary database removed."
}
validate_owned_resources() {
    local id directory resource label names containers
    # Capture the command result directly: a failed process substitution would
    # otherwise look like an empty installation and incorrectly pass the gate.
    containers="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")"
    while IFS= read -r id; do
        [[ -n "$id" ]] || continue
        directory="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$id")"
        [[ "$directory" == "$ROOT/deploy" || "$directory" == "$ROOT" ]] || die "Project '$PROJECT' belongs to another checkout: $directory."
    done <<< "$containers"
    for resource in network volume; do
        names="$(docker "$resource" ls --format '{{.Name}}')"
        while IFS= read -r id; do
            [[ "$id" == "${PROJECT}_"* ]] || continue
            label="$(docker "$resource" inspect --format '{{index .Labels "io.vaani.application"}}' "$id")"
            [[ "$label" == voice-agent ]] || die "Existing $resource '$id' has no Voice Agent ownership label; inspect/migrate it explicitly."
        done <<< "$names"
    done
}
