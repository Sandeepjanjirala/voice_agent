#!/usr/bin/env bash
# shellcheck source=scripts/lib/deployment.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/deployment.sh"
[[ $# -ge 1 ]] || die "Usage: bash scripts/restore.sh BACKUP.dump [--test | --replace-production --confirm-project PROJECT | --resume-replacement --confirm-project PROJECT] [--recordings ARCHIVE [--recordings-volume PROJECT_recordings_restore_NAME]]"
file="$(realpath -- "$1")"
shift
replace=false
resume=false
confirmation=''
recording_archive=''
recording_volume=''
while [[ $# -gt 0 ]]; do
    case "$1" in
        --test) ;;
        --replace-production) replace=true ;;
        --resume-replacement) resume=true ;;
        --confirm-project) [[ $# -ge 2 ]] || die "Missing project confirmation."; confirmation="$2"; shift ;;
        --recordings) [[ $# -ge 2 ]] || die "Missing recording archive."; recording_archive="$(realpath -- "$2")"; shift ;;
        --recordings-volume) [[ $# -ge 2 ]] || die "Missing staging volume."; recording_volume="$2"; shift ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done
load_runtime
lock_deployment
pending="$STATE_DIR/pending-restore"
assert_managed_path "$pending"
[[ "$file" != *$'\n'* && "$file" != *$'\r'* ]] || die "Backup paths cannot contain line breaks."
if "$replace" && "$resume"; then die "Choose either --replace-production or --resume-replacement."; fi
if "$replace" || "$resume"; then
    [[ "$confirmation" == "$PROJECT" ]] || die "Database replacement requires --confirm-project $PROJECT after reviewing recovery instructions."
    if "$resume"; then
        [[ -f "$pending" ]] || die "No interrupted database replacement is recorded. Use the normal guarded replacement workflow."
        [[ "$(state_field pending-restore 1)" == "$PROJECT" && "$(state_field pending-restore 2)" == "$DB_USER" && "$(state_field pending-restore 3)" == "$DB_NAME" ]] || die "Recovery state does not match the configured project/database."
        safety_backup="$(state_field pending-restore 4)"
        safety_hash="$(state_field pending-restore 5)"
        original_source="$(state_field pending-restore 6)"
        original_hash="$(state_field pending-restore 7)"
        [[ "$safety_hash" =~ ^[a-f0-9]{64}$ && "$original_hash" =~ ^[a-f0-9]{64}$ ]] || die "Recovery state has invalid backup checksums."
        assert_managed_path "$safety_backup"
        [[ -f "$safety_backup" && ! -L "$safety_backup" && "$(sha256sum < "$safety_backup" | cut -d ' ' -f 1)" == "$safety_hash" ]] || die "The verified pre-restore safety backup is missing or changed; preserve and review recovery state."
        source_hash="$(sha256sum < "$file" | cut -d ' ' -f 1)"
        [[ ( "$file" == "$original_source" && "$source_hash" == "$original_hash" ) || ( "$file" == "$safety_backup" && "$source_hash" == "$safety_hash" ) ]] || die "Resume accepts only the unchanged original archive or recorded pre-restore safety backup."
        # A recorded replacement already passed the normal call/job guard before
        # its database was dropped. Do not ORM-query a missing/partial database.
        # Every application container must still be stopped; never stop it here.
        app_ids="$(dc ps --all -q web worker beat)"
        while IFS= read -r app_id; do
            [[ -n "$app_id" ]] || continue
            app_status="$(docker inspect --format '{{.State.Status}}' "$app_id")"
            [[ "$app_status" == exited || "$app_status" == created ]] || die "Resume requires every web/worker/beat container already stopped; found $app_status."
        done <<< "$app_ids"
        verify_backup "$safety_backup"
    else
        [[ ! -e "$pending" ]] || die "An interrupted replacement is recorded. Review it and use --resume-replacement explicitly."
    fi
fi
verified_source_hash="$(sha256sum < "$file" | cut -d ' ' -f 1)"
verify_backup "$file"
restore_test "$file"
if [[ -n "$recording_volume" && -z "$recording_archive" ]]; then die "--recordings-volume requires --recordings ARCHIVE."; fi
if [[ -n "$recording_archive" ]]; then
    [[ -f "$recording_archive" && -s "$recording_archive" ]] || die "Recording archive is missing/empty."
    if [[ -f "$recording_archive.sha256" ]]; then
        (cd "$(dirname "$recording_archive")" && sha256sum -c "$(basename "$recording_archive").sha256" >/dev/null)
    fi
    args=(--rm --network none --read-only --user 0:0 --memory 512m --cpus 0.5
        --security-opt no-new-privileges:true --cap-drop ALL
        --cap-add CHOWN --cap-add FOWNER --cap-add DAC_READ_SEARCH
        -v "$ENV_FILE:/run/production.env:ro"
        -v "$ROOT/scripts/configure.py:/run/configure.py:ro"
        -v "$ROOT/scripts/recording_archive.py:/run/recording_archive.py:ro"
        -v "$recording_archive:/run/recordings.tar.gz:ro")
    destination=()
    if [[ -n "$recording_volume" ]]; then
        [[ "$recording_volume" =~ ^${PROJECT}_recordings_restore_[a-z0-9_]+$ ]] || die "Use a dedicated ${PROJECT}_recordings_restore_NAME staging volume."
        if docker volume inspect "$recording_volume" >/dev/null 2>&1; then die "Recording recovery refuses an existing volume; choose a new staging name."; fi
        docker volume create --label io.vaani.application=voice-agent --label "io.vaani.project=$PROJECT" --label io.vaani.recording-recovery=true "$recording_volume" >/dev/null
        args+=(-v "$recording_volume:/recovered")
        destination=(--destination /recovered)
    fi
    docker run "${args[@]}" "$APP_IMAGE:$APP_IMAGE_TAG" python /run/recording_archive.py \
        --env-file /run/production.env --archive /run/recordings.tar.gz "${destination[@]}"
    log "Encrypted recording archive verified. Live recordings were not changed. ${recording_volume:+Staged volume: $recording_volume. Review the recovery guide before promotion.}"
fi
if ! "$replace" && ! "$resume"; then exit 0; fi
if ! "$resume"; then
    drain_existing_backend
    safety_backup="$(backup_database)"
    restore_test "$safety_backup"
    safety_hash="$(sha256sum < "$safety_backup" | cut -d ' ' -f 1)"
    source_hash="$verified_source_hash"
    pending_temp="$(mktemp "$STATE_DIR/pending-restore.XXXXXX")"
    printf '%s\n' "$PROJECT" "$DB_USER" "$DB_NAME" "$safety_backup" \
        "$safety_hash" "$file" "$source_hash" > "$pending_temp"
    mv "$pending_temp" "$pending"
fi
log "Pre-restore backup: $safety_backup. Replacing database after explicit project confirmation."
[[ "$(sha256sum < "$file" | cut -d ' ' -f 1)" == "$verified_source_hash" ]] || die "Source archive changed after verification; replacement was not started."
# A fresh database avoids leaving post-backup tables behind. Only this
# application's validated database name is replaced, after explicit consent.
dc exec -T postgres dropdb -U "$DB_USER" --force --if-exists -- "$DB_NAME"
dc exec -T postgres createdb -U "$DB_USER" --owner "$DB_USER" -- "$DB_NAME"
dc exec -T postgres pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner --no-privileges --exit-on-error --single-transaction < "$file"
rm -- "$pending"
log "Database restored. Services stay stopped. Select compatible code, recover encrypted recording files if needed, then run deploy.sh/healthcheck.sh. No migration was assumed safe."
