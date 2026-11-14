# Telugu AI Calling Agent roadmap

The product is an incoming and outgoing Telugu calling agent managed from the
owner's Django application. LiveKit Console remains a testing interface.

| Phase | Scope | Current status |
| :--- | :--- | :--- |
| 1 | Independent Telugu voice worker | Core acceptance PASS: owner confirms Telugu/mixed speech and interruption; extended speech-quality checks INCOMPLETE (PHASE1_VERIFICATION.md) |
| 1.5 | Own Django browser voice studio | Local functional PASS: owner checklist, 92 tests, fixed browser regressions and live synthetic-microphone smoke pass; extended/production checks INCOMPLETE |
| 2 | Real incoming Indian phone calls | Offline foundation COMPLETE (14 Nov 2026): planning, read-only status, 66 mocked tests. Live telephony NOT STARTED: blocked on LiveKit and provider confirmations (PHASE2_IMPLEMENTATION.md) |
| 3 | Real outgoing Indian phone calls triggered through Django | Not started |
| 4 | PostgreSQL-backed Django dashboard, call records, transcripts, appointment booking and agent management | Not started |
| 5 | Production security, performance verification and deployment | Not started |

The voice worker and Django remain separate processes. Browser-session login uses
Django's existing auth tables and SQLite fallback; it is not the Phase 4 call schema.

## Phase 2 in steps

| Step | What | Status |
| :--- | :--- | :--- |
| 2.0 | Provider research and readiness report | Done (PHASE2_READINESS.md) |
| 2.1 | Offline Django telephony foundation: settings, plan, read-only status | Done |
| 2.2 | Written confirmations: LiveKit India SIP endpoint on our plan; provider account, inbound authentication and SRTP | Waiting on LiveKit and the provider |
| 2.3 | Call termination: polite goodbye and hang-up | Designed, tests and scenarios written first; implementation awaits review (CALL_TERMINATION_DESIGN.md) |
| 2.4 | Buy one number; enable `telephony apply`; provision trunk and dispatch rule | Needs owner approval. Not started |
| 2.5 | Real inbound test calls and the Phase 2 acceptance criteria | Needs owner's phone and voice. Not started |

No telephony provider account, number purchase, real phone call, live trunk, deployment,
commit, push or merge has happened in Phase 2. Stop for owner review before step 2.4.

## Still open from Phases 1 and 1.5

Naturalness, accents and noise, pauses and short replies, backchannels, turn-mode
comparisons, timed and repeated outages, cross-browser and concurrency checks. All need
the owner's voice or a second device. The production audit still reports six
configuration warnings; none of this is deployment approval.
