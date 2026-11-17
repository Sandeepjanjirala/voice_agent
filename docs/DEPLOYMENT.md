# Deployment

How the platform is meant to run in production, and what was checked on the
development machine. **Nothing is deployed.** Going live needs the owner's
approval, a server, a domain, and the switches listed under "Stays off".

The final review found additional live-integration and inbound fallback blockers.
Complete [PILOT_READINESS.md](PILOT_READINESS.md) before enabling providers; the
local checks below do not certify production readiness.

## What runs where

| Part | Runs on | Started by |
| :--- | :--- | :--- |
| Voice worker (`voice_agent/`) | LiveKit Cloud, region `ap-south` (LiveKit's advice, 16 Nov 2026) | `lk agent deploy`, from `Dockerfile` |
| Django web app (gunicorn) | Your server | `deploy/compose.yaml`, service `web` |
| Background jobs (Celery worker) | Your server | service `worker` |
| The one scheduler (Celery Beat) | Your server | service `beat`. Never run two |
| PostgreSQL 17 | Your server, no public port | service `postgres` |
| Redis 7 (job queue and shared cache) | Your server, no public port, password, no persistence | service `redis` |
| HTTPS (Caddy, automatic certificates) | Your server, ports 80 and 443 only | service `caddy` |

The worker and Django never import each other. The worker reaches Django only
through two signed HTTPS endpoints (`CALL_EVENTS_URL`, `INBOUND_CONTEXT_URL`).

## Go live, step by step

1. **Server.** Use a Linux VM in India (for example Mumbai) with Docker
   Engine and the compose plugin. Give it 2 vCPU and 4 GB of RAM. Open
   only ports 22 (SSH, by key), 80 and 443.
2. **Domain.** Point an A record (for example `calls.example.org`) at the
   server.
3. **Settings.**
   - Copy `deploy/production.env.example` to `deploy/.env.production` and fill
     it in.
   - Generate `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD` and
     `CALL_EVENTS_SECRET` fresh, with `python -c "import secrets;
     print(secrets.token_hex(32))"`.
   - Bring the contact keys over from the secrets manager
     ([CONTACT_KEYS.md](CONTACT_KEYS.md)).
   - Make the file readable by root only: `chmod 600`.
4. **Start:**
   `docker compose -f deploy/compose.yaml --env-file deploy/.env.production up -d --build`.
   The one-off `migrate` service applies migrations before `web`, `worker`
   and `beat` start.
5. **First user:**
   `docker compose -f deploy/compose.yaml --env-file deploy/.env.production exec web python manage.py createsuperuser`.
6. **Worker:**
   - Deploy the voice worker to LiveKit Cloud in `ap-south`.
   - Set `CALL_EVENTS_URL=https://<domain>/api/telephony/call-events/` and the
     same `CALL_EVENTS_SECRET` as its secrets.
7. **Check:**
   - `https://<domain>/api/health/` answers `{"status":"ok"}`.
   - Inside the server, readiness is all true:
     `docker compose ... exec web python -c "import urllib.request as u; print(u.urlopen(u.Request('http://127.0.0.1:8000/api/health/ready/', headers={'Host': '<domain>'})).read())"`.
   - Operations (in the dashboard) shows "Job queue (Redis) is reachable" and
     job runs within two minutes.
8. **Backups.** Schedule `deploy/backup.sh` and copy the dumps off the server
   ([DISASTER_RECOVERY.md](DISASTER_RECOVERY.md)). Do this before any real
   data arrives.
9. **HSTS.** After a week without HTTPS problems, raise `DJANGO_HSTS_SECONDS`
   to `31536000`.

## Security settings

| Setting | Production value | Why |
| :--- | :--- | :--- |
| `DJANGO_DEBUG` | `false` (forced by compose) | No tracebacks or settings in error pages |
| HTTPS redirect, secure cookies | On whenever DEBUG is off | Session and CSRF cookies travel over HTTPS only |
| `DJANGO_BEHIND_PROXY` | `true` (forced by compose) | Caddy ends TLS and sets `X-Forwarded-Proto` |
| HSTS | 3600 s at first, then a year | A short start can be undone if HTTPS breaks |
| `X-Frame-Options` | `DENY` | No embedding in other sites |
| Session length | 12 hours (`DJANGO_SESSION_HOURS`) | Shared office computers |
| `/api/health/ready/` | 404 from outside, works inside | It reveals which parts are down |
| `INTEGRATIONS_ALLOW_LOCAL` | `false` (forced by compose) | Actions can never reach private addresses |

`manage.py check --deploy` passes with these settings.
`tests/django/test_platform_pages.py::test_production_settings_pass_the_deploy_check`
runs that check in the test suite. HSTS preload is deliberately left off: it
commits the whole domain to HTTPS for months and is a go-live decision.

## Secrets

- **Where they live:** `deploy/.env.production` on the server (mode 600,
  git-ignored, excluded from Docker images by `.dockerignore`), and a copy in
  a secrets manager or password manager the owner controls. They are never
  in Git, in images, in logs, or in database backups.
- **Contact keys:** `CONTACT_ENCRYPTION_KEYS` and `CONTACT_LOOKUP_KEY` are the
  only secrets whose loss loses data. Back them up as described in
  [CONTACT_KEYS.md](CONTACT_KEYS.md), separately from the database dumps.
- **Integration credentials:** calendar and WhatsApp tokens and REST tokens
  are typed into the dashboard. They are stored encrypted with the contact
  keys and never shown again.
- **Rotating:**
  - Contact keys: [CONTACT_KEYS.md](CONTACT_KEYS.md).
  - `DJANGO_SECRET_KEY`: replace it and restart; everyone signs in again.
  - `CALL_EVENTS_SECRET`: change it on both sides at once.
  - Database and Redis passwords: change them in the env file, then
    `ALTER USER` (PostgreSQL), then restart.

## Monitoring and health checks

| Check | What it tells you | Who calls it |
| :--- | :--- | :--- |
| `GET /api/health/` | Django answers and the database responds | Docker health check (`web`), an uptime monitor over HTTPS |
| `GET /api/health/ready/` (inside only) | Booleans: database, cache, contact keys, job broker; 503 if any is false | The server's own monitor |
| `celery inspect ping` | The worker is alive | Docker health check (`worker`) |
| Operations page | Last run of each scheduled job, failures, queues, given-up webhooks and analyses | An admin, daily |
| Notifications (bell) | Failed actions and webhooks, missed callers, escalations | Staff |
| `docker compose ... logs -f web worker beat` | Request and job logs (no phone numbers or transcripts) | On demand |

An external uptime monitor (any HTTPS checker) should poll `/api/health/`
every minute and alert the owner. LiveKit Cloud's own dashboard covers the
voice worker.

## Privacy: what LiveKit Cloud keeps

Checked against LiveKit's documentation on 17 November 2026 (Agent insights,
Session recording options and Data residency):

- **Calls with an organisation profile normally ask LiveKit to keep nothing.** The worker starts
  every call with `record=False` unless the organisation has turned on "Let
  LiveKit Cloud keep call recordings…" under Privacy & settings. With
  `record=False`, the docs say no audio, transcript, traces or logs are
  uploaded.
  Legacy or fallback calls without an explicit profile recording choice follow
  the project's default. Verify this exception before enabling inbound traffic.
- **If an organisation turns it on**, and Agent observability is also on for
  the LiveKit project:
  - LiveKit keeps each call's audio, transcript, traces and logs for 30 days.
  - It stores them in the project's data region, which is the United States
    or the EU. India is not offered.
  - Only project admins can read them.
  - The worker also asks for PII redaction on those calls
    (`record={"redaction": True}`).
- **Deleting here does not delete there.** Erasing a contact or applying
  retention in this dashboard removes our copy only. LiveKit's copy expires
  after its 30-day window. The docs describe no way to delete a session
  earlier, and we do not claim to.
- **Always kept by LiveKit, whatever the setting:** "Session analytics" and
  "Session events" (room, participants, connection quality) come from the
  media servers and are visible to every project role. For phone calls the
  SIP participant can carry the caller's number. Their retention is not
  stated in the pages reviewed. Ask LiveKit before relying on it.
- **Models:** LLM calls through LiveKit Inference are zero data retention,
  according to LiveKit. Sarvam (speech) has its own terms. Check them before
  real calls.

## Stays off until the owner authorises it

| Switch | Effect while off |
| :--- | :--- |
| `INTEGRATIONS_LIVE_ENABLED=false` | Bookings, WhatsApp messages, REST requests and webhooks are checked and logged as "simulated"; nothing leaves the server |
| `CONTACT_DIALING_ENABLED=false` | Stored contacts cannot be dialled; campaigns and instant leads stay dry runs |
| `OUTBOUND_DIALING_MODE=allowlist` | Only numbers in `OUTBOUND_ALLOWLIST_NUMBERS` can be dialled, by Django and by the worker |
| `TELEPHONY_CONFIRMED` without `livekit_allowed_addresses_enforced` | No inbound trunk is created ([INBOUND.md](INBOUND.md)) |

## What was verified locally (17 November 2026)

On the development machine (Docker 29.8.2), with throwaway passwords and
`SITE_DOMAIN=localhost`, Caddy was published on loopback ports only. Nothing
was reachable from the network.

| Area | Result |
| :--- | :--- |
| Build | `deploy/Dockerfile.web` builds. The image runs as `appuser` and holds no `.env`, SQLite or test files |
| Start | All seven services start. `migrate` applies every migration, then `web` and `worker` report healthy |
| HTTPS | Plain HTTP gets a 308 to HTTPS. The login page answers 200 over HTTPS, with a `Secure; SameSite=Lax` cookie, `Strict-Transport-Security: max-age=3600`, `X-Frame-Options: DENY`, `nosniff` and `same-origin` |
| Static and health | Static files are served by Caddy. `/api/health/` answers publicly; `/api/health/ready/` is 404 publicly and reports the database, cache and broker as up inside |
| Jobs | Celery registers every job. Beat sends scheduled jobs and the worker completes them |
| Backups | `deploy/backup.sh` writes a verified dump. A restore into a scratch database had every migration and table |

Not verified: a real domain and certificate, a real server, the voice worker
on LiveKit Cloud, and load.
