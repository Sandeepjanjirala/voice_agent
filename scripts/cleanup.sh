#!/usr/bin/env bash
# shellcheck source=scripts/lib/deployment.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/deployment.sh"
apply=false
confirmation=''
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) ;;
        --apply) apply=true ;;
        --confirm-project) [[ $# -ge 2 ]] || die "Missing project confirmation."; confirmation="$2"; shift ;;
        *) die "Usage: bash scripts/cleanup.sh [--dry-run | --apply --confirm-project PROJECT]" ;;
    esac
    shift
done
load_runtime
lock_deployment
if "$apply"; then [[ "$confirmation" == "$PROJECT" ]] || die "Destructive cleanup requires --apply --confirm-project $PROJECT."; fi
days="$(config_get BACKUP_KEEP_DAYS)"
[[ "$days" =~ ^[0-9]+$ && "$days" -ge 1 ]] || die "BACKUP_KEEP_DAYS must be at least 1."
df -h "$ROOT"
docker system df
log "Expired application backups (retention $days days):"
if [[ -d "$BACKUP_ROOT" && ! -L "$BACKUP_ROOT" ]]; then
    newest="$(find "$BACKUP_ROOT" -maxdepth 1 -type f -name "$PROJECT-*.dump" -printf '%f\n' | sort | tail -n 1)"
    protected_backup=''
    [[ ! -f "$STATE_DIR/pending-update" ]] || protected_backup="$(sed -n '3p' "$STATE_DIR/pending-update")"
    restore_backup=''; restore_source=''
    if [[ -f "$STATE_DIR/pending-restore" ]]; then
        restore_backup="$(state_field pending-restore 4)"
        restore_source="$(state_field pending-restore 6)"
    fi
    while IFS= read -r -d '' file; do
        [[ "$(basename "$file")" != "$newest" && "$file" != "$protected_backup" && "$file" != "$restore_backup" && "$file" != "$restore_source" ]] || continue
        [[ "${file##*/}" =~ ^${PROJECT}-[0-9]{8}T[0-9]{6}Z-[0-9]+-[a-zA-Z0-9]{6}\.dump$ ]] || continue
        log "$file"
        if "$apply"; then
            rm -- "$file"
            for suffix in .sha256 .recordings.tar.gz .recordings.tar.gz.sha256; do
                [[ ! -f "$file$suffix" || -L "$file$suffix" ]] || rm -- "$file$suffix"
            done
        fi
    done < <(find "$BACKUP_ROOT" -maxdepth 1 -type f -name "$PROJECT-*.dump" -mtime "+$days" -print0)
    log "Abandoned partial files (report only; inspect before removal):"
    find "$BACKUP_ROOT" -maxdepth 1 -type f -name '*.partial' -mtime +1 -print
fi
log "Unused Voice Agent images (current/previous deployments protected):"
current=''; previous=''
[[ ! -f "$STATE_DIR/current" ]] || current="$(state_field current 1):$(state_field current 2)"
[[ ! -f "$STATE_DIR/previous" ]] || previous="$(state_field previous 1):$(state_field previous 2)"
while IFS= read -r image; do
    [[ "$image" == "$APP_IMAGE":v-* && "$image" != "$current" && "$image" != "$previous" ]] || continue
    [[ -z "$(docker ps -aq --filter "ancestor=$image")" ]] || continue
    log "$image"
    if "$apply"; then docker image rm "$image"; fi
done < <(docker image ls --filter label=io.vaani.application=voice-agent --filter "label=io.vaani.project=$PROJECT" --format '{{.Repository}}:{{.Tag}}')
if [[ -n "$(dc ps --status running -q web)" ]]; then
    log "Application retention preview (read-only; the policy also covers contacts/calls/transcripts):"
    dc exec -T web python manage.py retention
else
    log "Recording retention preview unavailable while web is stopped. Run manage.py retention after recovery."
fi
log "Recording deletion stays with the application's authorized retention policy. No recordings, volumes, shared build cache, or unrelated resources are removed."
if ! "$apply"; then log "Dry run only. Review before --apply --confirm-project $PROJECT."; fi
