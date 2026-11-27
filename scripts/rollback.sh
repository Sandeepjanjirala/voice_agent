#!/usr/bin/env bash
# shellcheck source=scripts/lib/deployment.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/deployment.sh"
[[ $# -eq 1 && "$1" == --confirm-schema-compatible ]] || die "Usage: bash scripts/rollback.sh --confirm-schema-compatible. Review migrations first: code rollback never restores/reverses database data."
load_runtime
lock_deployment
[[ ! -e "$STATE_DIR/pending-restore" ]] || die "Database replacement is incomplete. Follow restore --resume-replacement recovery before rolling back code."
slot=previous
# Failed updates never replace current: it remains the latest working image.
[[ ! -f "$STATE_DIR/pending-update" ]] || slot=current
[[ -f "$STATE_DIR/$slot" ]] || die "No rollback deployment is recorded."
[[ -z "$(git_repo status --porcelain)" ]] || die "Local Git changes prevent rollback; preserve/review them first."
image="$(state_field "$slot" 1)"
tag="$(state_field "$slot" 2)"
revision="$(state_field "$slot" 3)"
docker image inspect "$image:$tag" >/dev/null 2>&1 || die "Previous image is unavailable. Recover/build it explicitly; nothing was changed."
drain_existing_backend
backup="$(backup_database)"
log "Pre-rollback backup: $backup"
git_repo checkout --detach "$revision"
APP_IMAGE="$image"
APP_IMAGE_TAG="$tag"
export APP_IMAGE APP_IMAGE_TAG
dc config --quiet
start_backend
bash "$ROOT/scripts/healthcheck.sh" --public-if-configured
if [[ "$slot" == previous ]]; then record_current "$revision"; fi
rm -f -- "$STATE_DIR/pending-update"
log "Code/image rolled back to $revision. Current database and recordings preserved."
