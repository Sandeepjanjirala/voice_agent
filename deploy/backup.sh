#!/bin/sh
# Nightly PostgreSQL backup for deploy/compose.yaml. Run from the repository
# root on the server, for example from cron at 02:30:
#   30 2 * * * cd /srv/telugu-agent && sh deploy/backup.sh >> backups/backup.log 2>&1
#
# Writes backups/telugu_agent-<UTC time>.dump (pg_dump custom format) and keeps
# BACKUP_KEEP_DAYS days of them. The dump holds phone numbers only in encrypted
# form; the keys that decrypt them are NOT in it and must be backed up
# separately (docs/CONTACT_KEYS.md). Copy the dumps off the server too: see
# docs/DISASTER_RECOVERY.md.
set -eu

KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
DIR="${BACKUP_DIR:-backups}"
COMPOSE="docker compose -f deploy/compose.yaml --env-file deploy/.env.production"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$DIR/telugu_agent-$STAMP.dump"

umask 077
mkdir -p "$DIR"
$COMPOSE exec -T postgres pg_dump -U telugu_agent -d telugu_agent --format=custom --no-owner > "$FILE.partial"
# A dump that pg_restore cannot list is not a backup.
$COMPOSE exec -T postgres pg_restore --list < "$FILE.partial" > /dev/null
mv "$FILE.partial" "$FILE"
find "$DIR" -name 'telugu_agent-*.dump' -type f -mtime +"$KEEP_DAYS" -delete
echo "$(date -u +%FT%TZ) backup ok: $FILE ($(wc -c < "$FILE") bytes)"
