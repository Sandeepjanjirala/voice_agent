# Backups and disaster recovery

What to back up, how to restore, and what to do when something is lost. It is
written for the single-server setup in [DEPLOYMENT.md](DEPLOYMENT.md). Nothing
is deployed yet. The backup script and a restore were tested locally on
17 November 2026.

## What holds what

| Thing | Where | Lost if the server dies? | Backed up by |
| :--- | :--- | :--- | :--- |
| Organisations, users, employees, versions, contacts (numbers encrypted), leads, calls, results, actions, audit log | PostgreSQL volume `pgdata` | Yes | `deploy/backup.sh`, copied off the server |
| Contact keys (`CONTACT_ENCRYPTION_KEYS`, `CONTACT_LOOKUP_KEY`) | `deploy/.env.production` | Yes | The owner's password manager or vault, **never** with the dumps |
| Other secrets (Django, database, Redis, LiveKit, Sarvam, call events) | `deploy/.env.production` | Yes | Same vault. All can be regenerated, except that changing a provider key needs that provider's console |
| Job queue | Redis, no persistence | Yes, by design | Not needed: jobs are idempotent and are re-created from the database by the next Beat run |
| TLS certificates | Caddy volume `caddy_data` | Yes | Not needed: Caddy requests new ones |
| Code and configuration | Local Git | Yes, if this device is lost | Verified Git bundle copied to private off-device storage; no remote is configured as of 17 November 2026 |
| Voice worker | Source in Git; deployment not verified | Source recovery depends on the code backup | Redeploy only after source, credentials and deployment approval are recovered |

A database dump without the contact keys keeps everything except phone
numbers, which become unreadable. Keys without a dump hold no data. Keep them
apart, so that one stolen copy is not enough.

## Private off-device code backup

`git remote -v` returned no remotes at this checkpoint. A bundle on this
laptop protects against some repository damage, **not device loss**. No remote
or off-device upload has been created by this review.

Recommended procedure: keep a versioned, client-side encrypted copy of the
verified bundle in owner-controlled private storage, with MFA, restricted
access, and retention/version deletion unavailable to the upload credential.
Keep the encryption recovery key in a separate owner-controlled vault. Use
approved organisational storage and region; never a public repository or link.

1. After a clean commit, run `git bundle create <dated-path>.bundle --all`.
2. Run `git bundle verify <dated-path>.bundle` and record a SHA-256 checksum.
3. Clone it into an empty scratch directory, run `git fsck --full`, and compare
   the checked-out branch's HEAD with the source repository.
4. Encrypt locally using the organisation's approved backup tool, then upload
   the encrypted bundle and checksum to the private destination.
5. Download it on another device, decrypt, verify the checksum and repeat the
   clone/HEAD check. Record the date, location, commit and recovery owner.
6. Repeat after each accepted checkpoint. Keep independent dated versions.

A private Git remote is an additional option after the owner selects the
account and verifies repository visibility and access. Scan **all history**
before the first push; a clean staged-file scan does not certify old commits.
Do not use a destructive mirror push. Keep independent bundles even with a remote.
Git bundles exclude untracked/ignored data: database dumps, `.env.local`,
production secrets and contact keys need their separate backups below.

## Targets

- **Recovery point:** at most 24 hours of data lost (one nightly dump). Run
  the script more often if campaigns become busy.
- **Recovery time target:** 2 hours to a new server. This has not been measured
  on a replacement production server or from off-device storage.

## Backups

1. **Nightly dump on the server.** Use cron, as shown at the top of
   `deploy/backup.sh`. It writes `backups/telugu_agent-<time>.dump`
   (`pg_dump` custom format, file mode 600). It checks the dump with
   `pg_restore --list` and keeps 14 days (`BACKUP_KEEP_DAYS`).
2. **Off the server, every day.** Copy `backups/` to storage in another
   place, for example object storage in an Indian region with versioning and
   a 30-day lifecycle rule. Use a separate account whose credentials cannot
   delete old versions. The dumps hold personal data: keep them encrypted at
   rest and readable by the owner only.
3. **Keys, once, and after every rotation.** Follow
   [CONTACT_KEYS.md](CONTACT_KEYS.md) "Back up the keys". Note the
   fingerprints in the vault entry.
4. **Retention applies to backups too.** Erasures and retention deletes reach
   the backups as old dumps expire (14 days on the server, plus the off-site
   lifecycle). State this in the privacy notice.

## Restore drill (monthly, and before go-live)

This restores into a scratch database next to the live one. Nothing live is
touched.

```sh
F=backups/telugu_agent-<time>.dump
C="docker compose -f deploy/compose.yaml --env-file deploy/.env.production"
$C exec -T postgres createdb -U telugu_agent restore_drill
$C exec -T postgres pg_restore -U telugu_agent -d restore_drill --no-owner < "$F"
$C exec -T postgres psql -U telugu_agent -d restore_drill -tAc \
  "select count(*) from django_migrations; select count(*) from crm_contact;"
$C exec -T postgres dropdb -U telugu_agent restore_drill
```

On 17 November 2026 the drill was run against the local production stack:

- The backup verified.
- The restored database had all 39 migrations and 48 tables.
- The scratch database was then dropped.

Add each drill's date and counts to the list above.

## Runbooks

### The server is gone

1. Build a new server and point DNS at it ([DEPLOYMENT.md](DEPLOYMENT.md),
   steps 1 and 2).
2. Restore `deploy/.env.production` from the vault, including the same
   contact keys.
3. Start only the database:
   `docker compose -f deploy/compose.yaml --env-file deploy/.env.production up -d postgres`.
4. Load the newest off-site dump:
   `... exec -T postgres pg_restore -U telugu_agent -d telugu_agent --no-owner --clean --if-exists < <dump>`.
5. Start everything: `... up -d --build`. `migrate` brings the schema up to the
   code's version.
6. Check:
   - `manage.py contactkeys status` ends with "Every stored number can be read."
   - Readiness is all true.
   - Operations shows jobs running.
7. Tell staff that any changes made after the dump's time are lost.
   Calls in that window are still in LiveKit's session list and in the
   provider's call log.

### The database is corrupted or wrongly changed

1. Stop writers: `... stop web worker beat`.
2. Dump the damaged database first (`deploy/backup.sh`), for investigation.
3. Restore the last good dump with `--clean --if-exists` as above, then start
   the services.

### The contact keys are lost

If the vault copy exists, restore it ([CONTACT_KEYS.md](CONTACT_KEYS.md),
"Recover after losing `.env.local`").

If no copy exists anywhere, stored phone numbers cannot be recovered. This is
deliberate. Names, leads, calls and reports remain. Then:

1. Generate new keys.
2. Mark every contact as needing its number again.
3. Re-import from the source system.

Report this to the organisations affected.

### A secret leaked

- **Contact encryption key:** rotate it at once ([CONTACT_KEYS.md](CONTACT_KEYS.md)).
  Also treat existing dumps as exposed if the key and a dump may both have
  leaked.
- **Other secrets:** replace each one (see [DEPLOYMENT.md](DEPLOYMENT.md)
  "Secrets"), then revoke the old one at its provider.
- Check the access log (Settings → Access log) for what was done with it.

### Redis is lost

Restart it. Database-backed pending work can be recovered, but this is not
an exactly-once guarantee for external side effects:

- Pending webhook deliveries and analyses live in PostgreSQL.
- The next Beat run (within two minutes) re-queues them.
- Rate limits reset.

Before resuming live integrations, reconcile in-flight bookings/messages with
the provider: a provider may have accepted a request before the local success
was stored. Receiver deduplication and provider idempotency must be verified.

### LiveKit Cloud or the voice provider is down

Calls fail; Django and the dashboard keep working. Inbound callers get the
provider's failure tone. Unrouted calls that reach Django are listed as missed
callers. Nothing needs restoring when the service returns.

## Copies outside our control

Restoring or deleting our database does not change copies held elsewhere:

- **LiveKit Cloud:** session analytics, and recordings for organisations that
  allowed them. These expire on LiveKit's schedule; see
  [DEPLOYMENT.md](DEPLOYMENT.md#privacy-what-livekit-cloud-keeps).
- **The telephony provider's call records.**
- **Webhook receivers** that already received an event.

A deletion request has to be passed on to them separately.
