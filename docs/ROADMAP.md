# Telugu AI Calling Agent roadmap

The product is an incoming and outgoing Telugu calling agent managed from the
owner's Django application. LiveKit Console remains a testing interface.

| Phase | Scope | Current status |
| :--- | :--- | :--- |
| 1 | Independent Telugu voice worker | Core acceptance PASS: owner confirms Telugu/mixed speech and interruption; extended speech-quality checks INCOMPLETE (PHASE1_VERIFICATION.md) |
| 1.5 | Own Django browser voice studio | Local functional PASS: owner checklist, 92 tests, fixed browser regressions and live synthetic-microphone smoke pass; extended/production checks INCOMPLETE |
| 2 | Real incoming Indian phone calls | Not started; requires owner's approval |
| 3 | Real outgoing Indian phone calls triggered through Django | Not started |
| 4 | PostgreSQL-backed Django dashboard, call records, transcripts, appointment booking and agent management | Not started |
| 5 | Production security, performance verification and deployment | Not started |

The voice worker and Django remain separate processes. Browser-session login uses
Django's existing auth tables and SQLite fallback; it is not the Phase 4 call schema.
No telephony provider, number purchase, real phone call, deployment, commit, push or
merge is part of Phase 1.5. Stop for owner review before Phase 2.

Final review updated 13 November 2026. Owner confirmed all ten browser checklist items,
including temporary-network recovery. Existing engine, models and scenarios are preserved.
Production audit still reports six configuration warnings; this is not deployment approval.
Remaining: naturalness, accents/noise, pauses/short replies, backchannels, turn-mode
comparisons, timed/repeated outages, cross-browser and concurrency checks. See both
verification reports for evidence, fixed edge cases and limits. Await owner review
before any Phase 2 work.
