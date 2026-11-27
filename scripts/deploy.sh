#!/usr/bin/env bash
# shellcheck source=scripts/lib/deployment.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/deployment.sh"
configure_nginx=false
issue_certificate=false
domain_args=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain|--port|--project) [[ $# -ge 2 ]] || die "Missing value for $1"; domain_args+=("$1" "$2"); shift 2 ;;
        --configure-nginx) configure_nginx=true; shift ;;
        --issue-certificate) configure_nginx=true; issue_certificate=true; shift ;;
        --help) printf '%s\n' 'Usage: bash scripts/deploy.sh [--domain DOMAIN] [--port 8003] [--project voice-agent] [--configure-nginx] [--issue-certificate]'; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done
need docker
docker info >/dev/null 2>&1 || die "Docker is unavailable. Install/start it separately or grant access."
lock_deployment
[[ ! -e "$STATE_DIR/pending-restore" ]] || die "Database replacement is incomplete. Follow restore --resume-replacement recovery before deploying."
[[ ! -f "$STATE_DIR/pending-update" ]] || die "An interrupted update needs recovery. See pending-update state and rollback instructions before restarting services."
[[ ! -f "$STATE_DIR/pending-deploy" ]] || die "An interrupted first deployment needs review. Inspect migrations/service logs, stop owned application services, then remove only deploy/.state/pending-deploy before retrying."
config init --env-file "$(env_relative)" "${domain_args[@]}"
load_runtime
[[ -z "$(git_repo status --porcelain)" ]] || die "Checkout has local changes; review/commit them before creating a reproducible production image."
bash "$ROOT/scripts/preflight.sh"
if [[ -f "$STATE_DIR/current" ]]; then
    [[ "$(git_repo rev-parse HEAD)" == "$(state_field current 3)" ]] || die "Checkout differs from the deployed revision. Use update.sh or the documented rollback recovery."
    log "Installation exists. Preserving its image; use update.sh for code changes."
    dc up -d --no-recreate postgres redis
    wait_healthy postgres
    wait_healthy redis
    start_backend
else
    [[ -z "$(dc ps --all -q web worker beat)" ]] || die "Application containers exist without deployment state. Inspect the interrupted installation before retrying migrations."
    revision="$(git_repo rev-parse HEAD)"
    build_application "$revision"
    dc up -d --no-recreate postgres redis
    wait_healthy postgres
    wait_healthy redis
    log "Running migrations and static/private storage preparation in a controlled one-off container."
    printf '%s\n%s\n' "$revision" "$APP_IMAGE_TAG" > "$STATE_DIR/pending-deploy"
    dc run --rm --no-deps migrate
    start_backend
    record_current "$revision"
    rm -f -- "$STATE_DIR/pending-deploy"
fi
if "$configure_nginx"; then
    nginx_args=(--apply)
    "$issue_certificate" && nginx_args+=(--issue-certificate)
    bash "$ROOT/scripts/configure-nginx.sh" "${nginx_args[@]}"
fi
bash "$ROOT/scripts/healthcheck.sh"
log "Backend ready: $APP_IMAGE:$APP_IMAGE_TAG. Voice-worker deployment and calling activation remain separate."
