# Deployment architecture

For the existing Ubuntu VPS with host Nginx, use
[VPS_DEPLOYMENT.md](VPS_DEPLOYMENT.md). It contains the complete first-deploy,
Cloudflare, HTTPS, administrator, independent LiveKit worker, update and recovery
commands. This preparation has not connected to or deployed on the VPS.

## Shared VPS (recommended for this installation)

| Component | Deployment |
| --- | --- |
| Django / Gunicorn | `deploy/compose.shared.yaml`, loopback port 8003 |
| Celery worker / one Beat | Same backend image, separate services |
| PostgreSQL 17 | Private network, persistent project volume |
| Redis 7 | Private authenticated ephemeral queue/cache; database reconciliation |
| Static assets | WhiteNoise in the backend; collected during image build |
| Encrypted recordings | Persistent private project volume; authorised Django access |
| HTTPS | Existing host Nginx and Certbot; opt-in project-scoped helper |
| Voice worker | Separate LiveKit Cloud deployment using the root `Dockerfile` |

The Django image contains no local voice inference, audio models or Agents runtime.
It retains the LiveKit API client for telephony control. Django never imports the
voice loop, and the worker never imports Django/ORM. Four signed HTTPS endpoints
carry call events, employee profiles, audio uploads and inbound routing context.
See [the process-boundary ADR](adr/0001-two-process-boundary.md).

The new environment is generated once with mode 600. Existing keys are preserved.
Provider credentials are optional for a dashboard-only deployment. Calling and
recording keep their existing explicit controls. Nothing automatically buys a
number, provisions a trunk, enables incoming calls or deploys the voice worker.

Use [DEPLOYMENT_MEASUREMENTS.md](DEPLOYMENT_MEASUREMENTS.md) for current evidence
and limitations and [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) for recovery.
Historical tests from 17 November do not certify this new stack.

## Dedicated server alternative

`deploy/compose.yaml` and `deploy/Caddyfile` remain available for a **separate host
where ports 80/443 are free**. Do not run this variant beside host Nginx on the
shared VPS. It preserves its own Caddy/static volumes and a one-off migration
service. The shared-VPS scripts deliberately select `compose.shared.yaml`; they
must not be used to manage the dedicated variant or import its volumes implicitly.

For an owner-approved dedicated installation, first prepare a valid private
production env with the configuration helper, review dedicated Compose values and
its volume identities, then use:

```bash
docker compose -f deploy/compose.yaml --env-file deploy/.env.production config --quiet
docker compose -f deploy/compose.yaml --env-file deploy/.env.production up -d --build
docker compose -f deploy/compose.yaml --env-file deploy/.env.production exec web python manage.py createsuperuser
```

The dedicated Compose database name/user remains `telugu_agent`; it does not use
the shared variant's `POSTGRES_DB` / `POSTGRES_USER` overrides. Its recovery commands
must use its actual project name/database. Do not point shared cleanup/restore at
this deployment. Dedicated syntax was checked; no fresh dedicated runtime claim
is made in this change.

## Secrets and production controls

Keep Django, DB, Redis, contact encryption/lookup, recording encryption and callback
keys in a password manager separately from backups. Losing either contact or
recording encryption keys loses access to that encrypted data. Callback rotation
requires coordinated backend/worker changes. Changing a DB password in an env file
does not update the existing PostgreSQL role; rotate it explicitly in a maintenance
window. Never regenerate keys just because an update fails.

Production disables automatic development login and DEBUG. Host/CSRF origins are
explicit, cookies secure, redirect enabled, HSTS initially one hour, frame embedding
blocked and internal readiness denied at Nginx. WhiteNoise serves only static
assets; private recordings have no public filesystem alias.

## Privacy: what LiveKit Cloud keeps

Checked against LiveKit's documentation on 17 November 2026 (Agent insights,
Session recording options and Data residency):

- **Calls with an organisation profile normally ask LiveKit to keep nothing.** The worker starts
  every call with `record=False` unless the organisation has turned on "Let
  LiveKit Cloud keep call recordings…" under Settings. With
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
