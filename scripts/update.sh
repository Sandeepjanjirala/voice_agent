#!/usr/bin/env bash
# shellcheck source=scripts/lib/deployment.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/deployment.sh"
[[ $# -le 1 ]] || die "Usage: bash scripts/update.sh [origin/main]"
target="${1:-origin/main}"
[[ "$target" =~ ^origin/[a-zA-Z0-9._/-]+$ && "$target" != *..* ]] || die "Choose an origin remote branch, e.g. origin/main."
load_runtime
lock_deployment
[[ ! -e "$STATE_DIR/pending-restore" ]] || die "Database replacement is incomplete. Follow restore --resume-replacement recovery before updating."
[[ -f "$STATE_DIR/current" ]] || die "No successful deployment state; run deploy.sh first."
[[ ! -f "$STATE_DIR/pending-update" ]] || die "An interrupted update needs recovery before another update."
[[ -z "$(git_repo status --porcelain)" ]] || die "Checkout has local changes. Review/preserve them before updating; nothing will be discarded."
old_revision="$(state_field current 3)"
[[ "$(git_repo rev-parse HEAD)" == "$old_revision" ]] || die "Checkout differs from deployed revision. Reconcile it before update."
bash "$ROOT/scripts/preflight.sh"
guard_idle
backup="$(backup_database)"
restore_test "$backup"
log "Verified backup: $backup"
git_repo fetch --no-tags origin "${target#origin/}"
revision="$(git_repo rev-parse --verify 'FETCH_HEAD^{commit}')"
git_repo merge-base --is-ancestor "$old_revision" "$revision" || die "Target is not a fast-forward from the deployed revision; review branches manually."
if [[ "$revision" == "$old_revision" ]]; then
    bash "$ROOT/scripts/healthcheck.sh"
    log "Already running the requested revision."
    exit 0
fi
printf '%s\n%s\n%s\n' "$old_revision" "$revision" "$backup" > "$STATE_DIR/pending-update"
git_repo checkout --detach "$revision"
if ! bash "$ROOT/scripts/preflight.sh"; then git_repo checkout --detach "$old_revision"; rm -f -- "$STATE_DIR/pending-update"; die "New revision failed preflight; old checkout restored."; fi
if ! build_application "$revision"; then git_repo checkout --detach "$old_revision"; rm -f -- "$STATE_DIR/pending-update"; die "Build failed; old running services/checkout preserved."; fi
new_tag="$APP_IMAGE_TAG"
APP_IMAGE_TAG="$(state_field current 2)"
export APP_IMAGE_TAG
if ! guard_idle; then git_repo checkout --detach "$old_revision"; rm -f -- "$STATE_DIR/pending-update"; die "New work detected; update cancelled before stopping services."; fi
stop_admission_and_drain
# Recovery snapshot immediately before migrations, after admission is closed.
backup="$(backup_database)"
printf '%s\n%s\n%s\n' "$old_revision" "$revision" "$backup" > "$STATE_DIR/pending-update"
APP_IMAGE_TAG="$new_tag"
export APP_IMAGE_TAG
log "Applying migrations. Failure leaves services stopped for an explicit recovery decision."
dc run --rm --no-deps migrate
start_backend
bash "$ROOT/scripts/healthcheck.sh" --public-if-configured
record_current "$revision"
rm -f -- "$STATE_DIR/pending-update"
log "Updated to $revision. Previous image/backup retained; LiveKit worker was not changed."
