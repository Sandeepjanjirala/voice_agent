#!/usr/bin/env bash
# shellcheck source=scripts/lib/deployment.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/deployment.sh"
recordings=false
test_restore=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --recordings) recordings=true ;;
        --verify-restore) test_restore=true ;;
        --help) printf '%s\n' 'Usage: bash scripts/backup.sh [--verify-restore] [--recordings]'; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done
load_runtime
lock_deployment
if "$recordings"; then
    drain_existing_backend
    # Keep database rows and encrypted objects consistent while retention and
    # admission are paused. Resume even if archive creation fails.
    if "$RESUME_BACKEND"; then trap 'dc up -d --no-deps --no-build --scale beat=1 web worker beat' EXIT; fi
fi
file="$(backup_database)"
if "$test_restore"; then restore_test "$file"; fi
if "$recordings"; then
    # Stored files are application-encrypted. Encryption keys stay separate.
    dc run --rm --no-deps --entrypoint tar web -C /app/private_recordings -czf - . > "$file.recordings.tar.gz.partial"
    gzip -t "$file.recordings.tar.gz.partial"
    mv "$file.recordings.tar.gz.partial" "$file.recordings.tar.gz"
    (cd "$BACKUP_ROOT" && sha256sum "$(basename "$file").recordings.tar.gz" > "$(basename "$file").recordings.tar.gz.sha256")
fi
log "Verified backup: $file. Copy it off-server; store encryption keys separately. Retention is previewed by cleanup.sh."
