# Vaani on the shared Ubuntu VPS

Prepared on 27 November 2026. **Implementation is local; deployment is not
approved or performed.** Review this guide and the actual results in
[DEPLOYMENT_MEASUREMENTS.md](DEPLOYMENT_MEASUREMENTS.md) before running VPS commands.
Docker has recovered and its storage move to D: is verified. Both production
images have built successfully. Isolated Linux container startup, PostgreSQL
restore, encrypted persistence, service restarts and Nginx routing have passed.
After correcting a Gunicorn worker-recycling connection failure, all 3,200
authenticated requests passed without retries. These local results do not
certify capacity or compatibility on the shared VPS.

## What will run

```text
Browser / signed LiveKit callbacks
          |
Cloudflare Full (strict) -> existing host Nginx :443
                              |
                       127.0.0.1:8003
                              |
                    Gunicorn + Django :8000
                      |       |       |
                  PostgreSQL Redis  private encrypted recordings
                              |
                      Celery worker + one Beat

LiveKit Cloud: separate voice worker -> Sarvam STT / LLM / Sarvam TTS
                 |                  -> existing Vobiz SIP integration
                 +---- signed HTTPS requests to Django
```

Compose project `voice-agent` owns its network, PostgreSQL volume and private
recording volume. PostgreSQL and Redis have no host ports. No Caddy, media server,
local LLM, STT or TTS runs on this VPS. EduNexa continues using its existing Nginx
configuration and port 8002. Removed Video Studio resources are reported only.

The root `Dockerfile` builds the independent LiveKit worker. The backend uses
`deploy/Dockerfile.web`, a locked multi-stage image with the Django/API dependencies
only. `uv` and build caches remain in the builder. WhiteNoise serves collected
static files without a host bind mount. Web, jobs and migration share image layers.

## 1. Before the first deployment

These are **future commands for the owner after approval**, not commands executed
by this review. First commit/push the reviewed branch through your normal approval
process. A clone of unchanged `main` will not contain this automation yet.

Use a dedicated checkout on Ubuntu 22.04. Docker Engine/Compose v2, Nginx, Certbot,
Git, curl, OpenSSL and normal Ubuntu tools (`flock`, `ss`, `getent`, `free`, `df`)
must already work. The scripts do not install or restart shared host services.
No host Python, Gunicorn, Celery or virtualenv is required. Configuration runs in
a small Python container with no network; Django commands run inside the app image.
Docker may pull public images/packages during builds, so internet access is needed.

Use the same deployment account for later commands. On a normal rootful Docker
installation, `sudo bash ...` is the simplest consistent choice; the scripts run
Git as the checkout owner when invoked with sudo. Protect SSH and repository access
separately. Do not paste secrets into shell command arguments or Git remotes.

### Cloudflare DNS

In the **sandeepwebsolutions.in** zone create this single record:

| Field | Value |
| --- | --- |
| Type | A |
| Name | `voice` |
| IPv4 address | **Your VPS public IPv4 address** (not known to this repository) |
| TTL | Auto |
| Proxy status initially | DNS only, until the origin certificate works |
| Proxy status after HTTPS verification | Proxied (orange cloud) |
| SSL/TLS encryption mode | **Full (strict)** |

Do not change the `edunexa` or apex records. Remove/fix a conflicting **voice-only**
AAAA record if this VPS does not serve IPv6. Allow inbound 80 and 443 using the
existing firewall policy. Leave 8003, PostgreSQL and Redis closed externally.
DNS must resolve before preflight passes. DNS-only HTTP access is used for the
Certbot webroot challenge; after issuance the origin serves HTTPS and redirects HTTP.
Avoid a Cloudflare cache-everything, browser challenge or redirect rule on this
hostname's `/api/telephony/` callbacks. Do not cache authenticated dashboard pages.

References: [DNS record creation](https://developers.cloudflare.com/dns/manage-dns-records/how-to/create-dns-records/),
[proxy status](https://developers.cloudflare.com/dns/proxy-status/),
[Full (strict)](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/).

## 2. Deploy the backend

After the reviewed changes reach the intended branch:

```bash
git clone https://github.com/Sandeepjanjirala/voice_agent.git
cd voice_agent
# If reviewing before merge, first check out the approved deployment branch.
sudo bash scripts/deploy.sh
```

The command creates `deploy/.env.production` **only if absent**, mode 600, with
seven secure secrets: Django, PostgreSQL, Redis, contact encryption/lookup,
recording encryption and signed callbacks. It validates the existing file on
later runs without rewriting it. It then checks resources/ownership/ports, builds
the image, starts PostgreSQL/Redis, waits for health, runs migrations and storage
preparation in one container, starts web/worker/one Beat and checks health/static.
Images are tagged with the Git revision and timestamp. State is private under
`deploy/.state/`; backups and generated configuration are Git-ignored.

Default: `voice.sandeepwebsolutions.in`, port 8003, project `voice-agent`.
For a **fresh** installation only, change those using:

```bash
sudo bash scripts/deploy.sh --domain voice.example.com --port 8004 --project voice-agent
```

Changing flags on an existing installation never rewrites its environment. Change
the environment explicitly and review host/callback/port implications instead.
Do not rename a deployed Compose project: that selects different data volumes.

Preflight fails safely for a dirty checkout, another project's port/resources,
insufficient capacity, missing settings, bad permissions or unresolved DNS.
The initial local-only success message means **backend ready, public HTTPS pending**.
It does not claim phone calls work. Provider credentials are optional for this step.

### Edit settings privately

```bash
sudoedit deploy/.env.production
sudo chmod 600 deploy/.env.production
```

Set `ACME_EMAIL` for certificate issuance. Add approved provider credentials only
when their features are needed. Values containing `$`, `#` or backslashes must use
single quotes (`KEY='literal value'`); env files are parsed as data, never sourced.
Do not run `cat .env...`, `docker inspect` without a selective format, or verbose
`docker compose config` in shared terminals: those can expose secrets. Scripts use
`config --quiet` and diagnostics that print setting names, not secret values.

| Configuration | Requirement |
| --- | --- |
| `DJANGO_DEBUG`, eager tasks, local integration access | `false` |
| proxy, SSL redirect, secure cookies | `true` |
| allowed hosts / trusted CSRF origins | Exact site hostname / its HTTPS origin |
| DB/Redis passwords | Generated URL-safe values; preserve on every update |
| contact / recording encryption keys | Valid Fernet key lists; preserve old keys for existing data |
| `CALL_EVENTS_SECRET` | Same signing key on Django and Cloud worker |
| LiveKit backend URL/API credentials | Existing intended project; optional for dashboard-only |
| Sarvam / LLM / Gemini / integrations | Supply only credentials for features you enable |
| recording storage | Local private volume; new organisations record nothing by default |
| four callback URLs | Generated HTTPS URLs for this hostname; no localhost |

The template includes all supported deployment controls. Additional existing voice
tuning can be copied privately from the current approved worker configuration;
do not replace it with example tuning. If importing local data, restore its original
`CONTACT_ENCRYPTION_KEYS`, `CONTACT_LOOKUP_KEY` and `RECORDING_ENCRYPTION_KEYS`
**before** import. Generating fresh keys cannot decrypt old contacts or recordings.
Keep encrypted data and its key backup in separate protected locations.

## 3. Configure Nginx and HTTPS explicitly

Preview (does not change host services):

```bash
sudo bash scripts/configure-nginx.sh
```

Review the candidate file path printed by the command. To apply it and request
the first certificate after DNS and `ACME_EMAIL` are correct:

```bash
sudo bash scripts/configure-nginx.sh --apply --issue-certificate --check-renewal
sudo bash scripts/healthcheck.sh --public
```

If an existing valid matching certificate is present, omit `--issue-certificate`.
If it has a different Certbot directory name, add
`--certificate-name voice.sandeepwebsolutions.in-0001`. A certificate covering
other names is never replaced by this script. Issuance is not part of routine updates.
`--check-renewal` explicitly runs the staging-CA renewal dry run; it is not a real
production renewal or a guarantee about future DNS/firewall changes.

Only the project-named site in `sites-available`/`sites-enabled` and its scoped
Certbot reload hook are written. Existing unowned files/symlinks are refused.
`nginx -t` runs before reload. Failed application restores the prior Voice Agent
site and renewal hook; it never restarts Nginx or edits EduNexa. A newly issued
certificate is retained if later proxy verification fails, avoiding unnecessary
reissuance on retry. Review any reported Video Studio leftovers separately.

Nginx fixes Host/protocol headers and replaces untrusted forwarding headers.
It allows 64 MiB requests (app recording upload limit: 50 MiB), serves static via
WhiteNoise, allows same-origin microphone use and blocks detailed readiness.
Recordings are accessible through authorised Django endpoints only, never a media
alias. Cloudflare client IP is not trusted automatically; rate limiting sees the
proxy address until a separately reviewed trusted-IP configuration is installed.

After origin/public health pass, switch the Cloudflare record to **Proxied** and
repeat `healthcheck.sh --public`. Keep **Full (strict)**. If Nginx was configured
manually, set `DEPLOY_PUBLIC_HTTPS=true` so future health/update commands check it.
The helper marks successful HTTPS setup automatically.

For Error 526: verify the origin certificate covers this hostname, is unexpired,
has a trusted chain and matches its private key; verify DNS points at this VPS and
Nginx selects this site's certificate. Run the helper validation and inspect
`sudo certbot certificates` privately. Do not switch to Flexible or weaken TLS.
References: [Cloudflare 526](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-526/),
[Certbot webroot/renewal](https://eff-certbot.readthedocs.io/en/stable/man/certbot.html),
[Nginx proxy headers](https://nginx.org/en/docs/http/ngx_http_proxy_module.html).

## 4. Create the administrator

```bash
sudo bash scripts/manage.sh createsuperuser
```

Visit `https://voice.sandeepwebsolutions.in/` and sign in. On an empty database,
complete the organisation setup form with your organisation and first location.
Additional locations and team members can be managed afterward. There is no
production automatic login or default password. Then create an AI employee,
edit its instructions and publish it. Calling remains
separately gated. Gemini assistance and AI-generated employee drafts need their
provider credentials; manually editing employees and the dashboard do not.

`manage.sh` runs commands inside the correct running web container. Initial
migrations use the deployment's separate migration container and never require an
already-running web service. Do not run host `python manage.py ...` on this VPS.

## 5. Independent LiveKit Cloud worker deployment

**Do this only after a separate worker deployment approval.** No script here
deploys, creates or replaces the Cloud agent, buys a number, changes a trunk or
makes a call. The existing worker identity/voice tuning should be preserved.

First set the backend's approved LiveKit project credentials, `AGENT_NAME`, speech
and LLM configuration in its private environment. Make a separate restricted file:

```bash
sudo bash scripts/prepare-worker.sh
# Review privately. Existing output is never overwritten.
sudoedit deploy/.env.worker.production
```

This exports the four callback URLs/signing secret, dispatch name, approved
allowlist and present voice settings. It omits Django/DB/Redis/contact/recording
keys and the dashboard Gemini key. LiveKit Cloud **automatically supplies**
`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` for its selected project;
do not put those three reserved names in the Cloud secret file. The backend must
use credentials for the **same project**; the auto-generated worker key need not
be the same key. Preserve any additional existing worker tuning not represented
in the backend file. Secret values cannot be fetched back from Cloud.

Run the following on the trusted machine where LiveKit CLI is installed, from
this checkout. Securely transfer the restricted worker file if using a different
machine; never commit or paste it. Use the same non-root user as its CLI login,
and grant that owner-only file access rather than making it world-readable.

```bash
lk --version
lk cloud auth
lk project list
# Select the existing intended project; replace NAME with its listed name.
lk project set-default NAME
lk agent list
# Replace AGENT_ID with the EXISTING agent's ID, not its dispatch name.
lk agent config --id AGENT_ID
# Review livekit.toml's project/agent binding and the private secret file.
# Only after approval:
lk agent deploy --id AGENT_ID --secrets-file deploy/.env.worker.production
lk agent status --id AGENT_ID
lk agent logs --id AGENT_ID
```

If no Cloud agent exists, and creation/cost are separately approved, the alternative
is `lk agent create --region ap-south --secrets-file deploy/.env.worker.production`.
`ap-south` is Mumbai. Region is selected at creation and cannot be changed in place;
do not create a duplicate just to move an existing agent. Stop the old local test
worker intentionally once the approved Cloud worker is ready, so both do not
compete for the same dispatch identity. Never stop it during an active call.

Secret-only changes can use
`lk agent update-secrets --id AGENT_ID --secrets-file deploy/.env.worker.production`
after approval; this restarts the worker. Do not use `--overwrite` without a complete
reviewed secret set. Backend updates never run any of these commands.

Check all four URLs in the approved worker configuration:

```text
CALL_EVENTS_URL=https://voice.sandeepwebsolutions.in/api/telephony/call-events/
CALL_PROFILE_URL=https://voice.sandeepwebsolutions.in/api/telephony/call-profile/
CALL_RECORDING_URL=https://voice.sandeepwebsolutions.in/api/telephony/call-recording/
INBOUND_CONTEXT_URL=https://voice.sandeepwebsolutions.in/api/telephony/inbound-context/
```

Callbacks retain timestamped HMAC authentication and a 300-second freshness window.
Tests reject invalid/future/expired signatures and preserve application idempotency
for duplicate requests. There is **no global nonce replay ledger**. End/recording
events retry at most three times (5-second attempts, 0.5/1-second backoff); audio
upload uses three 60-second attempts with 1/2-second backoff. Answer notification
is one 2-second attempt; profile/inbound context fails closed without an indefinite
retry. A long backend outage can lose callbacks/audio. Use a quiet maintenance window;
this release does not change the callback schema or voice conversation behavior.

Commands/regions were checked against installed CLI help and current docs:
[agent deployment regions](https://docs.livekit.io/deploy/admin/regions/agent-deployment/),
[secrets](https://docs.livekit.io/deploy/agents/secrets/),
[deployment management](https://docs.livekit.io/deploy/agents/managing-deployments/).

## 6. Activate calling separately

Website health proves the backend works; Cloud worker registration proves a worker
is available; neither proves a phone call will connect. Use existing Vobiz routing
and credentials. Verify ownership of the caller ID, approved destinations, TLS/SRTP,
account entitlement and LiveKit dispatch identity with the provider before activation.
Do not switch provider or recreate existing SIP resources during an update.

Safe defaults remain `OUTBOUND_DIALING_MODE=allowlist`, empty approved destinations,
`CONTACT_DIALING_ENABLED=false` and no fabricated `TELEPHONY_CONFIRMED` values. The
application still checks DNC, consent, hours, duplicate calls, limits and cancellation.
Update the worker and backend allowlists consistently only for approved numbers.

An offline configuration plan (may correctly report missing telephony settings):

```bash
sudo bash scripts/manage.sh telephony outbound-plan
# For a later inbound configuration review only:
sudo bash scripts/manage.sh telephony plan
```

Read [OUTBOUND_TEST.md](OUTBOUND_TEST.md) and
[VOBIZ_SETUP.md](VOBIZ_SETUP.md) before any provisioning or real call. Incoming calls
remain deferred as requested. When revisiting them, independently verify provider
number provisioning, caller authentication, enforced LiveKit allowed addresses,
TLS/SRTP and the published employee route. Saving an allowlist does not demonstrate
that LiveKit enforces it. Never set the confirmation flag solely to pass validation.

## 7. Resources, recording policy and monitoring

Starting limits for this 1-vCPU / 4-GB shared host:

| Service | Memory cap | CPU cap | Initial concurrency |
| --- | --- | --- | --- |
| PostgreSQL | 384 MiB | 0.50 | 40 connections; 64 MiB shared buffers |
| Redis | 160 MiB | 0.15 | 96 MiB data, no eviction |
| Gunicorn | 512 MiB | 0.75 | 1 worker, 2 threads |
| Celery worker | 512 MiB | 0.50 | 1 process, prefetch 1 |
| Beat | 192 MiB | 0.15 | exactly 1 scheduler |
| One-off migration | 512 MiB | 0.75 | stops after completion |

These are caps, not measured usage or reserved capacity. Build/restore processes
need extra memory and disk. Preflight requires 1,200 MiB available RAM, at least one
CPU, 6 GiB free on the checkout filesystem, 3 GiB free on Docker's filesystem and
no more than 80% swap use. They are starting safety gates; expand them for your
database, recording retention and backups. A build on the 1-vCPU host may briefly
compete with EduNexa; choose a quiet window. Historical EduNexa usage is not used as
current evidence. Do not lower thresholds merely to force a failing deployment.

Gunicorn request-count recycling is disabled: authenticated local load reproduced
a connection drop when Gunicorn 23 recycled its only `gthread` worker. Planned
releases still replace the worker. Monitor sustained web memory use against its
512 MiB cap; the short local load test is not a memory-leak soak test.

```bash
sudo bash scripts/healthcheck.sh
sudo bash scripts/preflight.sh
sudo bash scripts/cleanup.sh --dry-run
```

Health checks probe Gunicorn HTTP/static, migrations, DB/Redis, Celery responsiveness,
Beat heartbeat, contact key format, encrypted private-storage I/O and container health.
They report restart counts, CPU/RAM, swap, disk and Docker storage for review;
preflight enforces the available-resource thresholds. Public HTTPS and origin
certificate expiry are checked after setup. They never invoke speech/LLM/telephony
providers. Structural key readiness cannot prove that every historical ciphertext
decrypts; run `sudo bash scripts/manage.sh contactkeys status` privately after restore.

Set an external HTTPS uptime monitor for `/api/health/` and review the Operations
page. Keep `/api/health/ready/` private. Monitor LiveKit separately using its console.
JSON container logs rotate at 10 MiB x 3 per service. Build cache is reported, never
globally pruned. Investigate repeated restarts or high swap before starting calls.

Recordings remain off for new organisations. If enabling them later, keep existing
consent/stop/discard requirements and choose retention in Settings. PostgreSQL holds
metadata; Fernet-encrypted files live in the persistent private volume shared by
web/jobs. Permissions are checked; requests are organisation-scoped. Retention jobs
respect the existing authorised policy and now exclude active-call recordings and
transcripts. Neither Nginx nor cleanup exposes or blindly removes recording files.

## 8. Updates, backups, rollback and cleanup

```bash
# Backend update: clean checkout, active-work checks, verified backup, build,
# maintenance window, migrations, startup, health; no worker or SIP changes.
sudo bash scripts/update.sh origin/main

# Private database backup and a networkless isolated restore drill.
sudo bash scripts/backup.sh
sudo bash scripts/restore.sh backups/voice-agent/VOICE_BACKUP.dump --test

# Optional paired DB + encrypted audio backup (temporarily pauses this app).
sudo bash scripts/backup.sh --recordings

# Explicit code rollback; review schema compatibility first. Does not undo data.
sudo bash scripts/rollback.sh --confirm-schema-compatible

# Preview only; applied cleanup requires the exact project confirmation.
sudo bash scripts/cleanup.sh --dry-run
sudo bash scripts/cleanup.sh --apply --confirm-project voice-agent
```

Replace `VOICE_BACKUP.dump` with the actual path printed by backup. Read
[DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) **before** production database restore
or recording recovery; database replacement is destructive and never automatic.
Back up keys separately in an owner-controlled vault, and send encrypted backups
off-server using your approved storage. Local backups alone do not cover VPS loss.
Scripts never upload them or purchase storage. Schedule backups only after review.

Updates are fast-forward only, record immutable image/revision state, keep the
previous image and back up before migrations. They stop when calls or important
queued work remain, close admission, drain jobs and check again. They have a short
maintenance window and **are not zero-downtime**. No automatic database rollback
can safely undo newer production data. A failed migration keeps services stopped
for an explicit recovery decision; do not delete pending-update state to force a run.

Redis stays ephemeral. Periodic database scans recover eligible call lists and
pending analyses, and idempotency is regression-tested with mocked Redis loss.
There is no general replay mechanism for arbitrary Celery messages or every running
job record. External side effects still require reconciliation: a provider can
accept work before its acknowledgement is saved. Physical Redis/worker restart
acceptance passed in an isolated local stack: Redis lost its synthetic ephemeral
marker as designed, and the worker and Beat became healthy again. That does not
prove recovery of every interrupted business task or external side effect.

Cleanup is scoped to this installation, protects current/rollback images and the
latest/interrupted-update backup, and never deletes volumes/shared build cache or
existing recordings. Backup expiry defaults to 14 days and requires explicit applied
cleanup. Abandoned partial files are reported for review. Authorised recording
retention continues through the normal scheduled policy; it is not shell `rm`.

## Troubleshooting

| Problem | Action |
| --- | --- |
| `No module named config` / missing dependencies | Use the root build context and supplied image/scripts; `WORKDIR /app` and locked backend dependencies are built in. Do not run commands from an arbitrary host directory. |
| Host `python` missing / web not running for migrations | Use `deploy.sh`; its Python helper and migration service run in containers. |
| Dirty checkout | Preserve changes, review and commit through normal approval before deployment; scripts never reset or discard them. |
| Port 8003 occupied | Identify its owner. On a fresh install choose another free loopback port; never stop EduNexa on 8002. |
| Disk/RAM/swap failure | Review `df -h`, `free -m`, `docker system df` and cleanup dry run; do not use global pruning. Check host free space as well as Docker's filesystem. |
| Migration failed / pending update | Inspect private service logs and pending state; decide compatible code rollback or reviewed data recovery. Existing volumes/keys remain. |
| HTTP redirects during local curl | Send the configured Host and `X-Forwarded-Proto: https`, or use the supplied health script. Public clients must use Nginx HTTPS. |
| HTTPS 526 | Check origin DNS/certificate/chain/hostname/key; keep Full (strict). |
| Static 404 | Health checks test WhiteNoise's built-in collected files; rebuild the intended image and inspect build collectstatic output. |
| Celery/Beat unhealthy | Verify Redis, worker responsiveness, heartbeat and queue. Exactly one Beat must run; no second scheduler on the host. |
| Can't decrypt imported contacts/audio | Restore original encryption keys from the vault. New keys cannot recover old data. |
| Website works, call fails | Check separately approved worker registration, callbacks, provider/trunk ownership, caller ID and destination restrictions. Health scripts deliberately place no calls. |

## Approval and remaining verification

Review the local diff and completed checks in the measurements report, then approve
commits/push separately. The Docker move, image builds, real PostgreSQL restore,
encrypted persistence, service restarts and repeated Compose startup were verified
locally. The full Ubuntu host scripts, shared-host capacity and public HTTPS still
need verification on the intended VPS after approval. Provide the VPS IP,
DNS/certificate email and private provider credentials, and approve the VPS
deployment and host Nginx/Certbot changes before running those commands.
Approve Cloud worker deployment independently and verify its existing identity.
Only after those steps approve any paid integration test, real call or inbound
activation. This preparation connected to no VPS and changed no Cloud deployment.
