# Telugu AI Calling Agent roadmap

The product is an incoming and outgoing Telugu calling agent managed from the
owner's Django application. LiveKit Console remains a testing interface.

Updated 14 November 2026.

| Phase | Scope | Current status |
| :--- | :--- | :--- |
| 1 | Independent Telugu voice worker | Core acceptance PASS: owner confirms Telugu/mixed speech and interruption; extended speech-quality checks INCOMPLETE (PHASE1_VERIFICATION.md) |
| 1.5 | Own Django browser voice studio | Local functional PASS: owner checklist, browser regressions fixed, live synthetic-microphone smoke pass; extended/production checks INCOMPLETE |
| 2 | Real incoming Indian phone calls | Integration BUILT and tested against a fake LiveKit: plan, status, apply, teardown, caller detection, optional hang-up. NO REAL INBOUND CALL YET: blocked on secure trunk authentication (see below) |
| 3 | Real outgoing Indian phone calls triggered through Django | Real calls WORK by command (two calls, 14 Nov 2026). Staff web page with live status and PostgreSQL-ready call records BUILT and tested with mocks; first real call from the page awaits owner review. Consent handling and calls to anyone else not started |
| 4 | PostgreSQL-backed Django dashboard, call records, transcripts, appointment booking and agent management | Started: call-record model and migration, verified on PostgreSQL 17. Transcripts, booking and agent management not started |
| 5 | Production security, performance verification and deployment | Not started |

The voice worker and Django remain separate processes. Automated suite: 265 tests.
Provider: Vobiz (the owner is an individual without GST, which rules out Plivo).

## Completed real-call checkpoints

| Date | Call | Result |
| :--- | :--- | :--- |
| 14 Nov 2026, 13:06 | First outbound call, 99 s | Connected with TLS, required media encryption and India routing. Two-way Telugu conversation worked. Problems: replies paused mid-sentence for up to 3.5 s, formal Telugu, one stock question repeated, English answered in Telugu, no hang-up |
| 14 Nov 2026, 16:23 | Second outbound call, 109 s | **Voice-quality checkpoint PASS (owner-confirmed):** natural Telugu-English, no broken audio, English answered in English, deliberate interruption honoured, AI hang-up worked. Event log: 0 pauses inside replies, 2 interruptions (both genuine), reply latency median 1.37 s |

Between the two calls the prompt, style sheet and greeting were rewritten, the hang-up
tool was enabled, and timing instrumentation was added. No audio, interruption, TTS,
voice or Vobiz setting was changed. Voice remains `kavitha`.

## Known open issue

**Mid-sentence pauses on the first call: cause unconfirmed, not reproduced.** Sarvam TTS
was ruled out for the long gaps and no words were lost, which points away from the SIP
media path. The leading hypothesis is false interruptions triggered by sound on the
caller's side (echo or noise). The instrumentation stays in place:

```powershell
.\.venv\Scripts\python.exe scripts\call_report.py
```

If it recurs, the report shows each pause and what preceded it; change one interruption
setting at a time and keep barge-in on.

## Outstanding work

### Inbound calling (Phase 2)

| Item | Status |
| :--- | :--- |
| Secure trunk authentication | **Blocker.** Vobiz cannot send a username and password on inbound calls, so the trunk needs LiveKit's address allow-list, which LiveKit support must enable for the project. Owner to ask LiveKit (VOBIZ_SETUP.md). Alternatives: LiveKit Ship plan, or a provider that sends credentials |
| India SIP endpoint on the current LiveKit plan | Outbound India routing worked on real calls; the inbound endpoint is still unconfirmed |
| Inbound-capable number | The trial caller ID is outbound only; inbound needs a purchased number |
| `telephony apply` against a real trunk | Not run; tested against a fake LiveKit only |
| First real inbound call and acceptance criteria | Not started |

### Outbound calling (Phase 3)

| Item | Status |
| :--- | :--- |
| Trigger a call from the Django web application | Built: staff-only page with confirmation, live status and End call. Tested with mocks; no real call placed from it yet |
| Call records: who, when, outcome, duration | Built: masked number, timestamps, talk time, SIP status, latency; written for web and command-line calls |
| More than one allowed number, with consent recorded | Not started. DLT and consent rules must be settled before any call to third parties |
| Noise cancellation on the outbound path | Not applied yet |
| Caller ID shown as "Spam Alert" by Jio on the trial number | Seen on the first call's recording; revisit with a purchased number |

### Dashboard and data (Phase 4)

PostgreSQL, call and transcript models, dashboard pages, appointment booking, agent
settings from the web application. Not started.

### Still open from Phases 1 and 1.5

Voice comparison by ear (samples in `voice-samples\`), accents and noise, long pauses,
backchannels, turn-detection comparison, timed network outages, cross-browser and
concurrency checks, and the six production configuration warnings. None of this is
deployment approval.

## Current milestone: outbound calls from the web application

| Item | Status |
| :--- | :--- |
| Staff-only calls page, explicit confirmation, live status, End call | Done; Playwright browser test passes |
| Call records with masked numbers, SIP status and latency | Done; migration verified on PostgreSQL 17 and SQLite |
| One allow-listed number, one call at a time (database constraint), no redial | Done |
| Worker reports outcomes to Django over signed HTTP | Done; worker never imports Django |
| First real call placed from the page | **Waiting for owner review** |

## Next milestone

**Secure inbound calls into the same records and page**, once LiveKit enables
allow-list enforcement or another verified authentication method is available.
In parallel: switch the local database to PostgreSQL, and decide the consent and DLT
rules that must exist before the agent calls anyone other than the owner.
