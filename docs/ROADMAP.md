# Telugu AI Calling Agent roadmap

The product is an incoming and outgoing Telugu calling agent managed from the
owner's Django application. LiveKit Console remains a testing interface.

| Phase | Scope | Current status |
| :--- | :--- | :--- |
| 1 | Independent Telugu voice worker | Core acceptance PASS: owner confirms Telugu/mixed speech and interruption; extended speech-quality checks INCOMPLETE (PHASE1_VERIFICATION.md) |
| 1.5 | Own Django browser voice studio | Local functional PASS: owner checklist, 92 tests, fixed browser regressions and live synthetic-microphone smoke pass; extended/production checks INCOMPLETE |
| 2 | Real incoming Indian phone calls | Integration BUILT and tested against a fake LiveKit (14 Nov 2026): plan, status, apply, teardown, caller detection, optional hang-up; 189 tests. NO REAL CALL YET: waiting on the owner's Vobiz account and LiveKit confirmations (VOBIZ_SETUP.md) |
| 3 | Real outgoing Indian phone calls triggered through Django | Minimal test path BUILT (14 Nov 2026): one trunk, one allowed number, one call per command; mocked tests only. NO REAL CALL YET (OUTBOUND_TEST.md). Dashboard, records and campaigns not started |
| 4 | PostgreSQL-backed Django dashboard, call records, transcripts, appointment booking and agent management | Not started |
| 5 | Production security, performance verification and deployment | Not started |

The voice worker and Django remain separate processes. Browser-session login uses
Django's existing auth tables and SQLite fallback; it is not the Phase 4 call schema.

## Phase 2 in steps

| Step | What | Status |
| :--- | :--- | :--- |
| 2.0 | Provider research and readiness report | Done (PHASE2_READINESS.md) |
| 2.1 | Offline Django telephony foundation: settings, plan, read-only status | Done |
| 2.2 | Written confirmations: LiveKit India SIP endpoint and allow-list enforcement on our plan; Vobiz number eligibility, prices, TLS and SRTP | Waiting on LiveKit and Vobiz; owner to ask (VOBIZ_SETUP.md) |
| 2.3 | Call termination: polite goodbye and hang-up | Done behind `AGENT_CAN_END_CALL` (off by default); 6 of 6 scenarios pass |
| 2.4 | Real `telephony apply` and `teardown` with dry-run, idempotency and rollback | Done, mocked tests only; not run against a live trunk |
| 2.4b | Buy one number and provision | Owner's account and payment. Not started |
| 2.5 | Real inbound test calls and the Phase 2 acceptance criteria | Needs owner's phone and voice. Not started |

Provider: Vobiz first (the owner is an individual without GST, which rules out Plivo).
No number purchase, real phone call, live trunk, deployment or push has happened in
Phase 2. Work is committed locally on branch `phase-2-inbound`.

## Test order

The first real call will be the outbound test to the owner's own mobile, because
outbound uses username-and-password authentication that Vobiz supports today. Secure
inbound resumes when LiveKit enables allow-list enforcement or another verified
authentication method exists. The final product still has both directions.

## Still open from Phases 1 and 1.5

Naturalness, accents and noise, pauses and short replies, backchannels, turn-mode
comparisons, timed and repeated outages, cross-browser and concurrency checks. All need
the owner's voice or a second device. The production audit still reports six
configuration warnings; none of this is deployment approval.
