# Backups and disaster recovery

This runbook covers the shared VPS stack in
[VPS_DEPLOYMENT.md](VPS_DEPLOYMENT.md). Run commands from the repository root.
Use the same deployment account as the initial installation. The examples use
`sudo` for the rootful Docker installation shown in that guide; do not switch
accounts or relax private environment/state permissions to make a command work.
The scripts use `deploy/compose.shared.yaml`, the selected Compose project,
and `deploy/.env.production`. They do not manage EduNexa or the LiveKit Cloud
worker. `deploy/compose.yaml` is the separate dedicated-server Caddy option;
do not use it on the shared Nginx host.

The current verification includes the full test suite against isolated
PostgreSQL 17.11, mocked providers, queue-loss recovery, encrypted recording
tests, and deployment command-boundary tests. See
[DEPLOYMENT_MEASUREMENTS.md](DEPLOYMENT_MEASUREMENTS.md) for final-image restore,
recording-volume recovery, and restart verification status. Earlier dated
audit results are historical and do not certify this deployment or a replacement
VPS.

## What must be recovered together

| Component | Storage | Recovery requirement |
| --- | --- | --- |
| Users, organisations, published employees, contacts, CRM, calls, results, recording metadata and audit records | Project PostgreSQL volume | Verified database dump |
| Private call audio | Project `private_recordings` volume | Matching archive of the encrypted files; the database contains metadata, not the audio |
| Contact numbers | Encrypted in PostgreSQL | Original `CONTACT_ENCRYPTION_KEYS` and `CONTACT_LOOKUP_KEY` |
| Recording encryption | `RECORDING_ENCRYPTION_KEYS` in the private environment | All keys needed by the selected archive; a new valid key cannot decrypt old audio |
| Database password and application/provider credentials | Private production environment and separate worker environment | Owner-controlled secret backup; preserve the existing database password on updates |
| Call-list progress and pending analysis | PostgreSQL, with an ephemeral Redis broker/cache | Restore PostgreSQL and run one Beat plus a worker; reconcile unresolved calls/jobs and external effects before enabling calling |
| Code, image identity and migration compatibility | Git plus `deploy/.state/current` and `previous` | Known source revision and compatible backend image; retain deployment state privately |
| HTTPS | Host Certbot certificate and Nginx configuration | Recover or reissue only this site's certificate with authorization; keep Cloudflare Full (strict) |

Database dumps also contain personal information that is not application
encrypted. Treat every dump and audio archive as private. Keep encrypted
off-server copies and their encryption keys under separate access controls.
Do not store key files inside the database/audio backup directory.

The scripts never rotate or regenerate existing keys. Before importing local
or historical data, recover the original contact lookup and encryption keys
and recording keys. A successful storage probe checks key format and a small
synthetic round trip; it does not prove that all historical records decrypt.

## Create and verify backups

For a database backup without interrupting the application:

```bash
sudo bash scripts/backup.sh
```

The script takes the deployment lock, writes a private PostgreSQL custom-format
dump under `backups/<project>/`, checks its archive catalog, and writes a SHA-256
sidecar. An unsuccessful dump remains a `.partial` file and is not published as
a completed backup. PostgreSQL supplies a consistent database snapshot.

To additionally restore the dump into an isolated disposable database:

```bash
sudo bash scripts/backup.sh --verify-restore
```

The restore test uses a new networkless PostgreSQL container with temporary
storage. It does not restore into the application database. It checks that the
archive can be restored and the migration table can be read. This is stronger
than reading the archive catalog, but it still requires application checks
such as contact decryption and authorized recording playback.

For a paired database and encrypted-audio backup, arrange a maintenance window:

```bash
sudo bash scripts/backup.sh --recordings --verify-restore
```

This option refuses active calls and important pending work, stops admission
and scheduling, drains the worker, and checks again before capturing the files.
If web was running at entry, it attempts to restart web, worker and one Beat when
the operation exits, including after a backup failure. An already stopped backend
stays stopped. Run the health check after a resumed backup. The audio
archive contains already encrypted recording files; encryption keys are not
included. External voice callbacks are affected during this window, so do not
begin while inbound traffic is expected.

Copy completed dumps, their checksum files, and any matching recording archives
to owner-approved storage on another machine. Back up secrets separately in a
vault. Verify a downloaded copy and perform a recovery drill before relying on
it. No off-server service, upload schedule, or paid storage is configured by
these scripts.

Choose the backup interval from the acceptable amount of lost work. A nightly
backup can lose almost a day; this is a planning choice, not a measured recovery
guarantee. Schedule database-only backups after confirming the desired interval.
Paired recording backups deliberately interrupt backend access.

## Test a selected backup

Replace the example path with the actual completed dump:

```bash
sudo bash scripts/restore.sh backups/voice-agent/voice-agent-YYYYMMDDTHHMMSSZ-PID-SUFFIX.dump --test
```

The default is also test-only if `--test` is omitted. Test mode verifies the
archive and restores it into a disposable isolated database; it does not stop
the backend or replace its data. The isolated container has a conservative memory
limit and uses a uniquely named scratch volume with restore-test ownership
labels. Leave enough free Docker storage for the restored database. A large
database may need an owner-reviewed drill on a larger scratch host; failure is
not permission to skip verification.

Keep a drill record containing the source revision, backup timestamp/checksum,
key fingerprints, test date, restored row counts, decryption/playback results,
elapsed time, and recovery owner. Do not put key values or caller details in it.

## Replace the application database explicitly

Replacing production data discards changes made after the selected backup.
First identify the correct backup, original keys, matching recording archive,
and compatible source revision. Take a current backup even when investigating
corruption. Keep call traffic away from the backend during recovery.

The command below is destructive and requires both the replacement flag and
the exact configured project name:

```bash
sudo bash scripts/restore.sh backups/voice-agent/voice-agent-YYYYMMDDTHHMMSSZ-PID-SUFFIX.dump \
  --replace-production --confirm-project voice-agent
```

The script tests the archive first, checks/drains outstanding work, and creates
a safety backup that also passes an isolated restore drill. It records a private
`deploy/.state/pending-restore` marker containing the project/database identity,
source and safety-backup paths, and their SHA-256 checksums before dropping data.
It then recreates only the configured application database
and restores into that empty database. It does not combine an older dump with
newer leftover tables. Database recreation and restore are not an atomic
switch: if they fail, services stay stopped and the safety backup is the
recovery source. Never retry by deleting a PostgreSQL volume.

If database creation or restore fails after replacement begins, fix the specific
failure first (for example, available disk space). Keep all web, worker and Beat
containers stopped. Then explicitly resume with either the unchanged original
archive or the recorded pre-restore safety backup:

```bash
sudo bash scripts/restore.sh /absolute/path/to/RECORDED_SAFETY_BACKUP.dump \
  --resume-replacement --confirm-project voice-agent
```

The recovery flag requires the pending marker, matching project/database, an
unchanged preserved safety backup, and a checksum-matching recorded source. It
refuses running, restarting or paused application containers and never stops them
automatically. Because the original replacement already passed the active-work
guard, this explicit retry does not query application tables that may now be
missing. It repeats the isolated restore test, recreates the selected database,
and leaves services stopped. A successful restore removes only the pending
marker; both archives remain. Cleanup protects archives referenced by this marker.
Ordinary deploy, update, rollback and management commands refuse pending recovery.
Do not delete or edit the marker to bypass a failed check. If the marker or its
verified safety backup is unavailable, stop and plan operator-led recovery from
the separately preserved backups rather than using this automatic resume path.

After a replacement, services remain stopped. Review migration compatibility
before starting them. The script does not automatically migrate an old backup,
reverse migrations, switch Git revisions, restore audio, or replace keys.
An interrupted update marker must be resolved through the update recovery
procedure rather than bypassed by an ordinary deployment.

For loss of the entire VPS, first prepare a replacement installation at the
chosen compatible source revision using [VPS_DEPLOYMENT.md](VPS_DEPLOYMENT.md).
Recover the original environment and keys before restoring any data. Establish
the private backend/database and verify the selected backup there before
changing public DNS or enabling calling. Do not attach an unrelated database
or use another application's project/volume names.

## Recover encrypted recording files

Audio and its database metadata must come from a compatible backup pair.
Recording recovery validates archive paths and file types and decrypts every
encrypted payload with the configured recording keys before files are accepted.
It does not overwrite a live recording volume during verification.

The restore tool can check the paired audio archive alongside the database:

```bash
sudo bash scripts/restore.sh backups/voice-agent/voice-agent-YYYYMMDDTHHMMSSZ-PID-SUFFIX.dump \
  --recordings backups/voice-agent/voice-agent-YYYYMMDDTHHMMSSZ-PID-SUFFIX.dump.recordings.tar.gz
```

To stage validated ciphertext in a newly named recovery volume:

```bash
sudo bash scripts/restore.sh backups/voice-agent/voice-agent-YYYYMMDDTHHMMSSZ-PID-SUFFIX.dump \
  --recordings backups/voice-agent/voice-agent-YYYYMMDDTHHMMSSZ-PID-SUFFIX.dump.recordings.tar.gz \
  --recordings-volume voice-agent_recordings_restore_20261127
```

Use a new project-specific recovery volume name. Existing volumes are refused.
Staging does not redirect the running application or delete its current audio.
Keep the original volume until restored metadata, key recovery, and authorized
playback have been checked. Never add a public Nginx media alias or export
decrypted audio as a shortcut.

For an explicitly reviewed recovery into an **empty** current recording volume,
use the same compatible image as the restored database. Review both volume
names before running this maintenance operation:

```bash
sudo bash -euo pipefail <<'RECOVER'
source scripts/lib/deployment.sh
load_runtime
lock_deployment
validate_owned_resources
staging=voice-agent_recordings_restore_20261127
target="${PROJECT}_private_recordings"
test "$(docker volume inspect --format '{{index .Labels "io.vaani.project"}}' "$staging")" = "$PROJECT"
test "$(docker volume inspect --format '{{index .Labels "io.vaani.recording-recovery"}}' "$staging")" = true
docker volume inspect "$target" >/dev/null
if [[ -n "$(dc ps --status running -q web)" ]]; then guard_idle; fi
dc stop -t 30 beat web
dc stop -t 90 worker
dc run --rm --no-deps --entrypoint python web manage.py deploycheck \
  --check inflight --require-idle --offline-workers
docker run --rm --network none --user 10001:10001 \
  -v "$staging:/source:ro" \
  -v "$target:/target" \
  "$APP_IMAGE:$APP_IMAGE_TAG" sh -ec '
    entries="$(find /target -mindepth 1 -maxdepth 1 -print -quit)"
    test -z "$entries"
    cp -a /source/. /target/
  '
RECOVER
```

The copy fails if the current volume contains any files. It neither merges nor
overwrites existing recordings. If the volume is nonempty, stop and review the
desired database/audio recovery pair; do not empty it to bypass this check.
Services remain stopped after the copy. Confirm the intended source revision
and database migrations, then use `sudo bash scripts/deploy.sh` and
`sudo bash scripts/healthcheck.sh --public` once pending recovery state is resolved.
Verify contact decryption and playback with an authorized organisation account.

## Roll back code separately from data

`update.sh` retains the prior application image and records the Git revision.
After reviewing schema compatibility, code-only rollback is:

```bash
sudo bash scripts/rollback.sh --confirm-schema-compatible
```

This returns to the previous successful image/revision while preserving the
current database and recording files. It takes a safety backup and performs
health checks. It does not reverse migrations or recover records changed or
deleted by an update. If the current schema is incompatible with the old code,
do not assert compatibility: plan a forward fix or an explicitly reviewed
database recovery instead.

When an update fails, `deploy/.state/pending-update` records the old/new source
revisions and recovery dump. `current` remains the last accepted deployment
until the new backend passes health checks. With a pending update, rollback
targets that last accepted deployment. A migration failure leaves services
stopped for a recovery decision; deployment does not silently restart old code
against an unreviewed schema.

An interrupted first deployment has no earlier accepted image. Its
`pending-deploy` marker prevents automatic retries over a partly started
application. Inspect the failure, the database migration state, and the image
revision. Stop and remove only this project's stopped application containers
as directed by the error, preserve both data volumes, and remove the marker
only after verifying that retrying the selected code is safe. Then rerun
`sudo bash scripts/deploy.sh`. Never remove deployment markers simply to suppress an
unexplained error.

## Redis loss and calls during recovery

Redis is an ephemeral broker/cache. Pending analysis and call-list progress live
in PostgreSQL. The periodic database scans recover that work and resume eligible
lists; stored call state and database constraints guard duplicate outbound
dispatch. They do not reconstruct every possible broker message or automatically
settle an interrupted job record. Cache loss also resets cache-based rate limits
and locks.
Mocked tests cover cache/message loss and repeated scheduler delivery; physical
Redis restart testing on the final stack remains a go-live check.

Before resuming, review active calls, entries still marked calling, pending
recording uploads, unfinished analyses and running job records. A stuck record
can intentionally block an update until an operator investigates it. Do not
clear Redis, delete job records or mark a call completed merely to bypass the
guard. Reconcile uncertain provider outcomes separately; no local test proves
exactly-once external effects after a network failure.

Backend maintenance is not zero downtime. Existing callbacks use signed
timestamps with a five-minute freshness window and application idempotency.
End-of-call and recording-decision events retry at most three times with
five-second HTTP timeouts; audio uploads retry at most three times with
sixty-second timeouts. Backoff adds a short delay. Profile/inbound lookups fail
closed when Django cannot be reached. Long maintenance can lose reports or
audio despite retries; the worker removes its temporary audio after upload
attempts. Updating the VPS does not drain or redeploy the separate LiveKit
worker, alter SIP routes, or prevent a new inbound caller from reaching it.

## Retention and cleanup

Recording remains off for new organisations. The existing per-organisation
policy defaults to 30 days for kept transcripts and recordings; zero means
keep indefinitely. Contacts, call records and audit entries default to zero.
Daily retention work follows that policy and preserves active-call recordings
and transcripts. Configure it through the organisation's settings, rather than
adding a filesystem deletion job.

Backup retention is separate (`BACKUP_KEEP_DAYS`, default 14). Preview first:

```bash
sudo bash scripts/cleanup.sh --dry-run
```

After reviewing the listed resources, explicit cleanup is:

```bash
sudo bash scripts/cleanup.sh --apply --confirm-project voice-agent
```

Cleanup protects the newest backup, the interrupted-update recovery backup,
and the accepted/current rollback images. It must not delete PostgreSQL data,
live recordings, keys, unrelated images, another application's volumes, or
shared build cache. Abandoned partial files are reported for review. Backups
can retain data erased from the application until their separate retention
expires; include this in the organisation's privacy policy.

Do not use `docker compose down -v`, global image pruning, or volume pruning as
a recovery step. They remove the evidence and data needed to recover safely.
