# Education platform acceptance and pilot readiness

Review date: 17 November 2026. Development checkpoint accepted by the owner.
This is not a production acceptance certificate. No paid calls, WhatsApp
messages, real school contacts, public deployment or Git push were performed.

## One consolidated acceptance gate

Run `uv run python scripts/acceptance.py` from the repository root. It runs the
complete synthetic regression suite, Django checks and migration drift checks.
`tests/django/test_acceptance.py` follows one admission enquiry through hiring,
voice selection, publishing, campus knowledge, signed inbound routing,
test-number outbound dispatch, analysis, follow-up, transcript deletion,
calendar booking, simulated Google/webhook execution and eager Celery reporting.
All records are synthetic. LiveKit, LLM and external HTTP are mocked.
The test-number fixture is synthetic too; it is not the owner's live number.

| Acceptance area | Automated evidence | What it does not prove |
| --- | --- | --- |
| Organisation/campus access | `test_tenancy_crm.py`, `test_platform_pages.py`, consolidated journey: cross-scope requests refused | Production staff and roles configured correctly |
| Hire, edit, publish, activate/pause | `test_workforce.py`, `test_calling.py`; immutable published snapshots | Spoken employee behaviour with a real provider |
| Employee voice/knowledge | `test_employee_calls.py`, journey: voice and campus knowledge reach worker profile | Sarvam audio quality on phone |
| Assigned inbound employee | `test_calling.py`, journey: signed routing, invalid signatures, paused/draft refusal | SIP allowlist enforcement or a live inbound call |
| Approved-number outbound | Fake gateway verifies only configured test destination, never contact number | Actual authorised number answering |
| Leads and follow-up | Intake consent/DNC tests, call analysis, journey | Consent evidence for real school contacts |
| Actions and results | `test_integrations.py`: allowed/refused/failed/simulated outcomes | Provider success under malformed responses, concurrent execution or uncertain timeouts |
| Calendar, REST, WhatsApp, webhooks | Mock HTTP requests, token refresh, template consent, signatures/retries; local calendar data persists | Approved accounts, delivery, provider permissions and concurrent booking safety |
| Summaries/privacy | Canned LLM, transcript masking/deletion, recording preference tests | Real-model summary accuracy or provider retention terms |
| Campaign/instant-lead dry runs | Consent, opt-out, hours, quotas and intake tests; no dispatch | Live campaign execution (disabled) |
| Celery/Redis | Eager task execution, job locks/reminders, Beat registration; E report records prior real local Redis/worker/Beat run | Production queue health, Redis-loss recovery or external exactly-once effects |
| Analytics/call records | Scoped conversations, immutable call context, usage aggregation | Actual provider billing reconciliation |
| Deployment/recovery | Production Django checks; E report records local compose/backup/restore drill | Real domain, host, load, off-device recovery or existing-data migration |

Fresh verification results are recorded below; historical E browser/compose
results remain in PROGRESS.md and DEPLOYMENT.md and are not described as fresh runs.

Fresh results: **722 passed on SQLite and 722 passed on disposable PostgreSQL
17**, including the new admission journey. Django system checks and migration
drift checks passed on both. Ruff lint and formatting passed; `uv lock --check`
and `uv sync --locked --dry-run` passed without dependency changes. Two warnings
come from a short synthetic JWT signing key in existing voice tests. No new
spoken debugger, LLM simulation, browser, Redis/Beat or production restore run
was made in this review. The voice implementation was preserved, not tuned.
Secret checks compare staged blobs against local credential values and common
credential/file patterns; they are not a full-history security certification.

## Actual blockers found in this review

1. **Inbound authentication is not verified.** Obtain project-specific LiveKit
   confirmation that allowed addresses are enforced; verify rejected unlisted
   SIP traffic before activating the route. Confirm current Vobiz signalling
   ranges, owned number and inbound SRTP. Do not set confirmation flags based
   only on a configuration value being saved.
   Also resolve the worker's fallback on a failed/404 inbound-context lookup:
   `run_inbound_call` currently starts with the fallback profile. Before a
   multi-campus inbound pilot, fail closed or use an explicitly approved,
   non-business fallback; test backend failure and unassigned/paused routes
   through the worker, including recording defaults. Backend routing tests
   alone do not prove the worker refuses such calls.
2. **Outbound HTTP safety needs hardening before any live integration.**
   `transport.send` checks the first URL, then uses `urllib.request.urlopen`,
   which follows redirects. A redirect destination is not checked; DNS is also
   resolved again during connection. Disable redirects or validate every hop
   without leaking credentials, and pin validated destinations or enforce a
   restrictive egress proxy/firewall. Add redirect/rebinding regression tests.
3. **Booking/result correctness needs hardening.** Local availability check and
   appointment insert have no reservation lock or database exclusion constraint.
   Concurrent requests can double-book. Google insert does not reserve/check a
   slot and accepts a 2xx response without an event ID as success. Add concurrent
   booking tests, validate provider results, and establish idempotency and
   reconciliation for timeout/crash-after-send cases before live bookings.
   Webhook delivery also does not re-check `Integration.is_active` for already
   queued rows. Fix and test revocation before enabling the global switch;
   disabling an integration alone cannot presently cancel pending deliveries.
4. **Live human transfer is not implemented.** Escalation creates a callback task
   and notification; it does not bridge a call. Vobiz REFER support must be
   confirmed. If required for the pilot, add a narrowly scoped, scenario-first
   transfer tool with an approved campus target, rejection/no-answer fallback
   and audited result. Otherwise explicitly agree to callback-only escalation.
5. **No Git remote or verified off-device backup exists.** The refreshed local
   bundle is not disaster protection against device loss. Complete the private
   copy and download/restore procedure in DISASTER_RECOVERY.md.
6. **Production cutover is unverified.** Real infrastructure, external alerts,
   approved privacy/retention settings, secret recovery, migrated data and
   restored-backup checks remain required. Local clean-schema tests are not a
   production data migration.

Current LiveKit references: [inbound trunk authentication](https://docs.livekit.io/telephony/accepting-calls/inbound-trunk/)
requires project enablement for allowed addresses; [cold transfers](https://docs.livekit.io/telephony/features/transfers/cold/)
use SIP REFER and depend on provider support. These docs do not establish
Vobiz support or prove that enforcement is enabled for this project.

## Exact sequence before a limited pilot

1. Complete and independently restore the private code/secret/database backups.
2. Close HTTP safety and booking/result blockers with targeted tests. Keep
   `INTEGRATIONS_LIVE_ENABLED=false`, `INTEGRATIONS_ALLOW_LOCAL=false` and
   stored-contact dialing disabled throughout this work.
3. Configure one synthetic pilot campus and published employee in the dashboard:
   approved facts, voice, opening hours, escalation owner, permitted actions,
   least-privilege staff and retention choices. Routine campus and workflow
   edits use configuration; novel provider capabilities may still require code.
4. Confirm LiveKit allowlist enablement and Vobiz number/ranges/SRTP. With owner
   approval for provisioning and testing, prove unlisted-source rejection and
   correct listed-source routing. Verify signed backend endpoints, caller
   masking, after-hours handling and paused/unassigned route behaviour.
5. Obtain explicit approval for **one** published-employee outbound call to
   the existing authorised test number. Verify its masked identity in the UI,
   chosen voice/knowledge, lifecycle, summary accuracy and no real contact dial.
   Keep transcripts/recording consistent with the approved privacy setting.
6. Use owner-approved test accounts: Google OAuth client, consent/scopes,
   refresh token and writable calendar; Meta business account, phone ID,
   supported Graph version, token permissions and approved template; fixed
   HTTPS REST endpoint/token/schema; webhook URL/signing key and receiver
   deduplication. Disable all unapproved integrations before enabling the
   global live switch. Enable only the approved test operation, verify the
   provider-side result, then switch off. Sending WhatsApp needs separate approval.
7. Decide callback-only versus live transfer. Live transfer needs its own
   implementation, simulation/debugger verification and authorised provider test.
8. Rehearse migration using a consistent copy of the existing database in an
   isolated PostgreSQL 17 instance. Preserve IDs, relations, encrypted values
   and original contact keys; compare per-table counts, versions, permissions,
   call context and consent/DNC records. Verify `contactkeys status`, sequence
   values and application reads. Take a verified backup, stop writers for final
   cutover, migrate/import, repeat checks, and keep a rollback snapshot. Never
   substitute a clean database install for this migration verification.
9. After deployment approval, validate HTTPS/domain, worker region, private DB
   and Redis, one Beat process, job freshness, provider failures, external
   health/backup-age alerts and owner notification. Restore an off-device dump
   with separately recovered keys; measure recovery time and lost-data window.
10. Restrict the first pilot to the agreed campus, staff, duration, spend and
    consented participants. Confirm stop/rollback ownership. Unrestricted
    dialing and campaigns remain disabled until separately authorised.

The platform is substantially implemented for configurable school workflows.
Live integrations, speech behaviour and production recovery remain conditional;
the passing synthetic gate must not be used to claim otherwise.
