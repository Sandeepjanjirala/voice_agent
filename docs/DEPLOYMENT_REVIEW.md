# Deployment review — 27 November 2026

**Initial local preparation report; production deployment remains unapproved.**
The measurements below record the initial working-tree verification. The final
deployment-only PR and subsequent security fixes are reviewed separately in
[PR_REVIEW.md](PR_REVIEW.md). No VPS or Cloud deployment, real call, purchase or
merge was performed during preparation.

The owner's Docker Desktop storage move from C: to D: is complete and verified.
Both production images, isolated Linux startup, backup/restore, encrypted
persistence, service restarts and Nginx routing have passed local checks.
After correcting a Gunicorn worker-recycling connection failure, 3,200
authenticated requests passed without retries. This report separates implemented
controls, executed tests and pending verification; it is not a go-live sign-off.

Use these accompanying runbooks:

- [VPS deployment](VPS_DEPLOYMENT.md): prerequisites, complete first-deployment,
  Nginx/Certbot, administrator, worker and update commands.
- [Disaster recovery](DISASTER_RECOVERY.md): backup verification, explicit database
  replacement, staged recording recovery, rollback and cleanup.
- [Deployment measurements](DEPLOYMENT_MEASUREMENTS.md): measured image sizes,
  runtime/load evidence and the resolved local Docker interruption.

## 1. Existing repository architecture

Vaani already has the intended two-process boundary. Django and Django REST
Framework own authentication, organisations/locations, employee profiles,
knowledge, CRM/contact imports, call metadata/results, permissions, persistence
and the dashboard. PostgreSQL stores durable state; Redis supports Celery and
cache functions. A Celery worker and Beat run scheduled/reconciled background
work. The backend uses the LiveKit API client to control telephony.

The separate `voice_agent/` process owns the real-time LiveKit Agents loop,
Sarvam STT/TTS and configured LLM calls. Vobiz SIP remains the telephony provider.
Voice events, profile lookups, recording uploads and inbound context cross the
boundary through signed HTTPS endpoints. Django does not import the voice loop;
the voice worker does not import Django or its ORM. See the
[process-boundary ADR](adr/0001-two-process-boundary.md).

## 2. Deployment problems identified

| Evidence reviewed | Problem or operational gap |
| --- | --- |
| Original `deploy/compose.yaml` and `deploy/Caddyfile` | Caddy claimed public 80/443, conflicting with the existing host Nginx. Static serving depended on that Caddy arrangement. |
| Original `deploy/Dockerfile.web`, `pyproject.toml` and measured baseline | The backend installed the voice-processing stack despite excluding development dependencies. The completed baseline image was 1,383,046,491 bytes. |
| Original Compose commands | Three Gunicorn workers and Celery concurrency two lacked evidence for the shared 1-vCPU host. Container logs, Redis growth and service resource limits needed bounds. |
| Original `deploy/backup.sh` | Backup catalog verification existed, but there was no complete locked deployment/update/recovery workflow, isolated restore drill or reviewed cleanup preview. |
| Deployment startup and health coverage | Fresh migrations, static preparation, private storage, worker responsiveness, Beat activity and interrupted-deployment recovery needed one consistent entry point. |
| Original recording mounts and `apps/crm/retention.py` | Shared persistent private recording storage needed explicit production wiring; recording/transcript retention did not exclude active calls. |
| `config/celery.py` documentation and job implementation | The comment claiming no job dialed a phone was inaccurate: eligible call-list jobs can dispatch under the existing controls. Updates therefore need real work guards. |
| Configuration and host setup | Secure first-time secret generation, preservation of existing keys, scoped Nginx/Certbot changes, port/resource ownership and resource preflight were not automated together. |

The existing one-off migration pattern, Vobiz integration, encrypted recording
implementation and call safeguards were retained. The work did not introduce
FastAPI, SQLAlchemy, Kubernetes or a new telephony provider.

## 3. Problems fixed

The shared-VPS Compose variant binds Gunicorn only to `127.0.0.1:8003`, leaves
PostgreSQL/Redis private, uses WhiteNoise for static assets and retains a separate
migration container. Backend and voice dependency groups are installed from
`uv.lock` into separate multi-stage images. The backend excludes Agents, audio
plugins and local inference models while retaining the API client, existing
assistant, spreadsheet import and recording-storage dependencies.

The scripts now initialize/validate secrets without host Python, lock mutations,
check capacity and ownership, wait for datastore health, migrate before startup,
prepare storage and verify all backend services. Updates require a clean checkout,
fast-forward source progression, verified backups and two active-work checks with
admission/scheduling paused between them. Failed migrations retain recovery state
and leave services stopped. No automatic database rollback is attempted.

Nginx/Certbot changes are separate explicit actions. They validate ownership and
`nginx -t`, restore the previous Voice Agent configuration on failure and leave
EduNexa configuration untouched. Recording retention now excludes active calls.

Authenticated load also exposed a connection drop during Gunicorn 23 `gthread`
request-count recycling. The single-worker configuration now disables that
automatic recycling instead of masking the failure with request retries or
increasing concurrency. Planned deployments still replace the worker, and the
web memory cap and monitoring remain in place.

## 4. Files created and modified

The deployment work is concentrated in these paths; review the actual local diff
before approving a commit.

| Area | Deployment files |
| --- | --- |
| Build and dependencies | `Dockerfile`, `deploy/Dockerfile.web`, `pyproject.toml`, `uv.lock`, `.dockerignore`, `.gitattributes` |
| Production services | New `deploy/compose.shared.yaml`, `deploy/gunicorn.conf.py`, `deploy/container_health.py`; updated dedicated `deploy/compose.yaml` |
| Configuration/proxy | `deploy/production.env.example`, `.env.example`, `.gitignore`, `deploy/nginx/http.conf.template`, `deploy/nginx/https.conf.template`, `scripts/configure.py`, `scripts/configure-nginx.sh` |
| Automation | `scripts/deploy.sh`, `update.sh`, `preflight.sh`, `healthcheck.sh`, `backup.sh`, `restore.sh`, `rollback.sh`, `cleanup.sh`, `lib/deployment.sh`, `recording_archive.py`, `manage.sh`, `prepare-worker.sh`; compatibility wrapper `deploy/backup.sh` |
| Backend readiness/retention | `config/settings.py`, `config/celery.py`, `apps/operations/deployment.py`, `apps/operations/management/commands/deploycheck.py`, supporting package initializers, `apps/operations/tasks.py`, `apps/crm/retention.py` |
| Tests and CI | `.github/workflows/ci.yml`, `tests/deployment/`, `tests/django/test_deployment.py`, `tests/test_deployment_dependencies.py`, additions to call-list/calling/recording and voice callback tests |
| Documentation | `README.md`, `docs/DEPLOYMENT.md`, `docs/VPS_DEPLOYMENT.md`, `docs/DISASTER_RECOVERY.md`, `docs/DEPLOYMENT_MEASUREMENTS.md`, this review |

The checkout already contained unrelated dashboard/UI, tenancy/workforce,
assistant, telephony-view, browser-test and associated documentation/test changes.
Those were preserved, including pre-existing `apps/tenancy/bootstrap.py` and
`apps/workforce/static/workforce/employees.css`. They are **not attributed to this
deployment work**. Some documentation files were already dirty and also received
deployment edits; a whole-file Git diff is not an authorship boundary. No voice
prompts, speech models, conversation behavior or turn-taking settings were changed
for this deployment task.

## 5. Final production architecture

```text
Cloudflare Full (strict)
        -> existing host Nginx :443
        -> 127.0.0.1:8003 -> Gunicorn/Django :8000
                             | PostgreSQL (persistent private volume)
                             | Redis (private, authenticated, bounded)
                             | Celery worker + one Beat
                             | encrypted private recording volume

LiveKit Cloud voice worker -> Sarvam STT / configured LLM / Sarvam TTS
                          -> existing Vobiz SIP integration
                          -> four signed HTTPS backend endpoints
```

Project `voice-agent` owns its network and volumes. EduNexa retains port 8002 and
its existing host Nginx site. No local voice models, self-hosted LiveKit media
server or Caddy runs in this shared-VPS stack. The dedicated-server Caddy option
remains available for a separate host with free ports 80/443; the shared scripts
deliberately do not manage that variant.

## 6. Environment configuration requirements

`deploy/.env.production` is generated once with mode 600. Fresh installations get
`DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`,
`CONTACT_ENCRYPTION_KEYS`, `CONTACT_LOOKUP_KEY`, `RECORDING_ENCRYPTION_KEYS` and
`CALL_EVENTS_SECRET` in the formats the application expects. Existing files,
database passwords and encryption keys are never regenerated by deployment or
updates. Importing existing data requires its original contact and recording keys.

The template configures the domain/port/project, exact allowed hosts and CSRF
origin, trusted proxy mode, production cookies, HTTPS redirect, logging and
resource/retention controls. Environment files are parsed as data, not shell
programs. Compose interpolation also clears conflicting exported settings from
another application before using the validated file.

LiveKit, Sarvam, the chosen LLM, Gemini assistant and other integration credentials
are supplied privately only for enabled features; they are not mandatory for a
dashboard-only installation. Worker secrets are prepared separately and omit
backend database/contact/recording keys. LiveKit Cloud supplies its reserved
project credentials; confirm that the backend and worker use the same intended
project and dispatch identity. All four worker callback URLs use the approved
production HTTPS hostname. See the complete
[configuration and worker instructions](VPS_DEPLOYMENT.md).

## 7. Docker image sizes before and after optimization

| Measurement | Result |
| --- | --- |
| Original backend image | **1,383,046,491 bytes / 1,319.0 MiB**, built locally from captured pre-change source |
| Original runtime package count | 125 |
| Optimized locked backend package installation | 85 packages; installation completed |
| Optimized completed image size | **519,045,978 bytes / 495.0 MiB** |
| Measured image-size reduction | **62.47%** (864,000,513 bytes smaller) |

Removing voice dependencies from the backend is verified by the lock graph and
tests. Both final image sizes above were measured using Docker image inspection.
The optimized build succeeded after recovery from the earlier local Docker storage
failure, when the Windows C: drive filled; see
[measurement evidence](DEPLOYMENT_MEASUREMENTS.md).

## 8. Actual container RAM and CPU measurements

The first isolated Docker Desktop sample measured **421.28 MiB aggregate mean
idle RAM** and **483.07 MiB peak aggregate RAM** under four concurrent clients
requesting login, static and readiness URLs. Over 30 seconds, all **1,590 requests
returned HTTP 200**, with median 63 ms and p95 125 ms; the peak sampled aggregate
CPU was 42.43% of one core.

A separate authenticated dashboard sample exposed a connection failure during
Gunicorn request-count recycling. The diagnostic replay produced **2,005 HTTP 200
responses plus one failed transport attempt**, retried successfully. It peaked
at 451.53 MiB aggregate RAM and 107.68% of one core across services. This is not
an error-free result or evidence of spare capacity on the shared 1-vCPU VPS.
With request-count recycling disabled, the final **3,200 authenticated GETs all
returned HTTP 200 in 43.41 seconds**, with **zero transport failures or retries**.
Four concurrent clients visited Home, AI employees, Call results and Make calls;
median latency was 47 ms and p95 was 78 ms. Peak aggregate RAM was **471.17 MiB**
and peak sampled aggregate CPU was **130.82% of one core**, including scheduled
worker/health activity. The fixture contained one organisation and contact, with
no employees or calls. This establishes the local regression fix, not throughput
on a single-core shared host. See the measurements report for retained evidence.

These are small synthetic database samples on Docker Desktop, not production
traffic or voice load. No current VPS measurement was taken, and historical
EduNexa usage is not treated as current evidence. These configured limits are
caps, not observed usage or reservations:

| Service | Memory cap | CPU cap | Initial concurrency |
| --- | --- | --- | --- |
| Gunicorn | 512 MiB | 0.75 CPU | 1 worker, 2 threads |
| Celery worker | 512 MiB | 0.50 CPU | 1 process, prefetch 1 |
| Beat | 192 MiB | 0.15 CPU | 1 scheduler |
| PostgreSQL | 384 MiB | 0.50 CPU | 40 connections, 64 MiB shared buffers |
| Redis | 160 MiB | 0.15 CPU | 96 MiB data limit, no eviction |
| One-off migration | 512 MiB | 0.75 CPU | One controlled invocation |

Keep the initial concurrency until the actual shared host is measured. The
1-vCPU host can experience contention during a build or sustained dashboard load.

## 9. Disk-space requirements and the local Docker move

Preflight currently requires at least **6 GiB free on the checkout filesystem**
and **3 GiB free on Docker's filesystem**, plus **1,200 MiB available RAM**, at
least one CPU and no more than 80% swap use. If both paths share a filesystem,
the checks apply to that same available space; they are not an extra 9-GiB
reservation. These are starting safety gates, not a measured total capacity
requirement. Allow additional space for the actual database, recordings, backup
retention, rollback image, build cache and isolated restored database.

A local Docker storage failure interrupted the first build. The owner recovered
Docker and moved its disk to a secondary drive using Docker Desktop's supported
settings. A cold disk backup and before/after read-only volume-content inventories
were verified; existing services and backups were preserved. Builds and isolated
tests resumed only after verification. Detailed machine inventory, disk paths and
backup locations are retained privately, not published in this repository.

## 10. Security improvements

The stack adds loopback-only web publication, private datastores, explicit
resource/log limits, non-root backend processes, read-only application filesystems
where appropriate, dropped capabilities and `no-new-privileges`. Build contexts
exclude secrets, local data, recordings, dumps and tooling output.

Nginx fixes the expected Host/protocol headers and replaces client-supplied
forwarding headers. Detailed readiness remains private; encrypted audio has no
public media alias. Production cookies, CSRF origins, security headers and
same-origin microphone policy are preserved. Access logging avoids query strings.
The Nginx helper manages only its owned site and certificate hook and never edits
EduNexa or replaces a certificate covering other names.

Signed callbacks retain timestamped HMAC checks, the 300-second freshness window
and existing application idempotency. There is **no global nonce replay ledger**.
Tests reject invalid, future and expired signatures; that does not establish
exactly-once effects across provider/network failures. No call or paid AI request
is made by deployment health checks.

## 11. Recording storage and retention improvements

Local recordings use a persistent private volume shared by the required backend
services, with encrypted objects and preserved Fernet keys. Storage readiness
performs a tiny synthetic encrypted round trip and rejects unsafe permissions.
Recordings remain off for new organisations, with existing consent, stop/discard
and organisation-scoped playback/download controls intact.

The existing organisation policy continues to govern retention; active-call audio
and transcripts are now excluded from expiry. Cleanup previews policy results
without deleting recording files itself. Database-only backups remain available;
paired database/audio backups pause this application's admission and jobs to
capture a consistent pair. Recovery validates archive paths, object types and
Fernet decryption before staging ciphertext into a **new empty volume**. It never
overwrites the live recording tree automatically. Promotion requires the reviewed
empty-target procedure in [disaster recovery](DISASTER_RECOVERY.md).

## 12. Test results and evidence

| Executed check | Result and scope |
| --- | --- |
| Full local SQLite pytest suite | **1,130 passed, 2 skipped, 352 warnings, 72.76 seconds**. This is newly executed repository regression evidence, not a production deployment test. |
| Full suite using real PostgreSQL 17.11 | **1,131 passed, 2 skipped, 352 warnings, 101.85 seconds**. A new isolated Docker database on a random loopback port was verified before execution. Includes the subsequently added image-ownership regression test. Existing local database and environment fingerprints are unchanged. |
| Focused deployment scripts and archive recovery | **50 passed**. Real Bash control flow with fake Docker/host command boundaries, plus real synthetic Fernet/archive validation; included in the broader test work, not additive production scenarios. |
| Nginx control-flow/rollback harness | **9 passed**, using fake host paths and mocked service/certificate commands. |
| Real Nginx syntax and routing | **Both generated HTTP/HTTPS configurations passed `nginx -t`; 7 routing/security checks passed**, in isolated Nginx 1.31.6 with a synthetic certificate. Covers redirects, ACME webroot, blocked readiness/hidden paths, spoofed header replacement, security headers, static proxying and upload limits. This is not verification of the VPS's installed Nginx or its real certificate. |
| Shell syntax and ShellCheck | Passed for deployment scripts/helpers; ShellCheck 0.11.0. |
| Ruff lint and formatting | Passed; 222 files checked/formatted consistently. |
| Locked dependency consistency | Offline `uv lock` check passed. |
| Compose syntax | Shared and dedicated variants passed `docker compose config --quiet` with fixture settings. |
| Backend image builds | Both baseline and optimized images completed and were measured locally; the optimized image runs as UID 10001 without voice/ML/development packages. |
| Final Gunicorn regression and authenticated load | **10 focused deployment/dependency/process tests passed** after the configuration fix; the rebuilt image then served **3,200 authenticated GETs with zero failures or retries**. The full PostgreSQL suite above preceded this narrow configuration correction. |
| Isolated five-service Linux startup | Migrations exited successfully; web, PostgreSQL, Redis, Celery worker and Beat became healthy. Readiness, login and collected static assets returned 200, HTTP redirected to HTTPS, and private audio paths returned 404. |
| Real database/archive restore drill | A 245,400-byte custom PostgreSQL dump restored 54 migrations and an encrypted synthetic contact that decrypted with the original fixture keys. A 265-byte encrypted private-object archive also decrypted. This used synthetic bytes, not a real recorded call. |
| Physical restart and persistence checks | PostgreSQL/web/worker/Beat restart and forced container recreation retained volume identities and exact contact ciphertext/private-object hashes. Redis lost its ephemeral marker as designed; worker/Beat health recovered. Repeated Compose startup left container IDs unchanged. This is not an end-to-end run of the Ubuntu host deployment scripts. |
| Separate voice-worker image | Built successfully: **1,031,899,102 bytes / 984.1 MiB**, 75 packages, UID 10001. Offline imports, CLI help and cached `download-files` checks passed; no agent connection, voice session or provider inference was started. |
| Production Django system check | No errors. `security.W005` reports that HSTS does not include subdomains; preload remains intentionally off with its existing warning suppression. Wider HSTS scope/preload needs a separate domain-policy decision. |

The two local skips concern Windows POSIX-permission/symlink limitations; Linux
verification remains necessary. Warnings were reported, not suppressed or counted
as failures. Regression coverage includes auth/organisation boundaries, encrypted
contacts and recordings, callback signatures, call controls, duplicate prevention,
retention, queue-loss reconciliation and process/dependency separation. Shell
tests cover fresh/repeated startup, resource/port/DNS failures, invalid settings,
failed backups/migrations, active-work guards, restore defaults and cleanup scope.

CI now specifies locked installs, SQLite/PostgreSQL test variants, Bash syntax,
ShellCheck and the isolated Nginx harness. That configuration has not been pushed
or executed in GitHub Actions in this task. Provider-backed simulations, debugger
sessions and real calls were not run; voice conversation behavior was unchanged.

All isolated QA services were stopped after verification. Test evidence, images
and data volumes were retained. Existing unrelated services were restored to
their original running or stopped states.

## 13. Remaining limitations

- Full Ubuntu host deployment/update/rollback orchestration remains unexecuted
  on a real VPS. Its Bash control flow was tested with mocked host boundaries;
  the actual application containers, restore, persistence and restart paths
  were tested separately on Docker Desktop.
- No actual VPS capacity, public DNS ownership, origin certificate, firewall,
  Cloudflare routing or shared-host compatibility has been verified remotely.
- Updates and paired recording backups have maintenance downtime. Pausing Django
  does not drain the independent Cloud worker or stop a new inbound caller.
  Callback retries are finite; long outages can lose events or uploaded audio.
- Redis remains ephemeral. Database reconciliation recovers eligible known work;
  arbitrary messages and all interrupted job states are not automatically rebuilt.
  Uncertain provider outcomes and stale running-job records require review.
- Valid encryption-key formats and synthetic readiness do not prove every old
  ciphertext decrypts. Historical contact/audio recovery needs preserved keys and
  authorised application-level verification.
- A code rollback requires schema compatibility. Database replacement destroys
  newer database changes and is a separate explicitly confirmed action. No
  zero-downtime, zero-loss or exactly-once guarantee is claimed.
- Existing Vobiz/SIP ownership, TLS/SRTP, approved caller ID/destinations, worker
  registration and inbound security remain external verification tasks. Backend
  health alone does not certify telephony.

## 14. Exact Cloudflare DNS settings

| Setting | Value |
| --- | --- |
| Zone | `sandeepwebsolutions.in` |
| Record type / name | `A` / `voice` |
| Address | The VPS public IPv4 address, which must be supplied by the owner |
| TTL | Auto |
| Initial proxy state | DNS only until the origin certificate is working |
| Final proxy state | Proxied, followed by public HTTPS verification |
| SSL/TLS mode | **Full (strict)** throughout |

Do not change EduNexa or apex records. Review only a conflicting `voice` AAAA
record if this host does not serve IPv6. The [VPS guide](VPS_DEPLOYMENT.md) includes
certificate issuance, callback/cache rules and Error 526 troubleshooting.

## 15. First-time VPS deployment commands

After the reviewed changes are approved and made available on the selected remote
branch, the core future workflow is:

```bash
git clone https://github.com/Sandeepjanjirala/voice_agent.git
cd voice_agent
# Check out the approved deployment branch if it has not been merged.
sudo bash scripts/deploy.sh
sudoedit deploy/.env.production
sudo bash scripts/configure-nginx.sh
# Review the generated configuration, DNS and ACME_EMAIL first.
sudo bash scripts/configure-nginx.sh --apply --issue-certificate --check-renewal
sudo bash scripts/healthcheck.sh --public
sudo bash scripts/manage.sh createsuperuser
```

The existing remote `main` does not gain these uncommitted local changes
automatically. Domain/port overrides, existing-certificate handling and all
prerequisites are in the [first-deployment guide](VPS_DEPLOYMENT.md). These commands
are instructions for a later approved action, not evidence they were executed.

## 16. Independent LiveKit Cloud worker commands

Prepare a separate restricted worker file with
`sudo bash scripts/prepare-worker.sh`, review it privately and retain the existing
project/agent identity and approved voice settings. The exact login, project
selection, existing-agent binding, deploy/status/log commands are in
[the worker deployment section](VPS_DEPLOYMENT.md#5-independent-livekit-cloud-worker-deployment).

Those CLI commands were checked against installed help/current documentation.
Use `ap-south` (Mumbai) only for an approved new agent where appropriate; do not
create a duplicate to move an existing agent. A Cloud deployment or secret update
can restart the worker and requires separate approval. Nothing in backend
deployment provisions SIP resources or enables real calling.

## 17. Future update commands

```bash
sudo bash scripts/update.sh origin/main
sudo bash scripts/healthcheck.sh --public
```

Choose a quiet maintenance window and a clean reviewed checkout. The updater
preserves environment keys/volumes, verifies backups, refuses active calls or
important unfinished work, builds an immutable image tag, migrates and verifies
health. It updates only the backend stack; LiveKit and Vobiz are independently
controlled. Interrupted-state recovery is documented rather than silently forced.

## 18. Backup, restore, rollback and cleanup commands

```bash
sudo bash scripts/backup.sh --verify-restore
sudo bash scripts/backup.sh --recordings --verify-restore
sudo bash scripts/restore.sh backups/voice-agent/ACTUAL_BACKUP.dump --test
sudo bash scripts/rollback.sh --confirm-schema-compatible
sudo bash scripts/cleanup.sh --dry-run
# Only after reviewing the preview:
sudo bash scripts/cleanup.sh --apply --confirm-project voice-agent
```

Use the actual completed backup path. Read the full
[recovery runbook](DISASTER_RECOVERY.md) before using
`--replace-production --confirm-project voice-agent` or staging/promoting a
recording archive. Store encryption keys separately from encrypted off-server
backups. Backup cadence, off-server destination and any paid storage remain owner
choices. No global Docker prune, unrelated volume deletion or automatic database
restore is part of routine maintenance.

## 19. Remaining approval and verification steps

1. **Complete:** Docker Desktop's disk-location move and post-move inventory and
   all-volume content verification. The original running services were restored.
2. **Complete:** isolated image builds, PostgreSQL/Redis startup, persistence,
   recovery, repeated Compose startup, resource samples and the final no-retry
   authenticated load check after the worker-recycling correction.
3. Review the deployment diff separately from the preserved pre-existing work.
   Approve committing/pushing/merging through the normal project process.
4. Supply the VPS IP and private credentials, confirm DNS/certificate choices and
   approve the first VPS deployment. Approve host Nginx/Certbot changes explicitly.
5. Approve LiveKit Cloud worker deployment separately, confirming its existing
   project/identity and callback configuration. Verify provider ownership and
   telephony restrictions before any approved test call or inbound activation.

The current status is **local preparation and verification complete; real VPS
verification and production deployment pending approval**.
