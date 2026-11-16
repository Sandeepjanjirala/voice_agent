# Telugu AI Calling Agent roadmap

The product is an incoming and outgoing Telugu calling agent managed from the
owner's Django application. LiveKit Console remains a testing interface.

Updated 14 November 2026.

| Phase | Scope | Current status |
| :--- | :--- | :--- |
| 1 | Independent Telugu voice worker | Core acceptance PASS: owner confirms Telugu/mixed speech and interruption; extended speech-quality checks INCOMPLETE (PHASE1_VERIFICATION.md) |
| 1.5 | Own Django browser voice studio | Local functional PASS: owner checklist, browser regressions fixed, live synthetic-microphone smoke pass; extended/production checks INCOMPLETE |
| 2 | Real incoming Indian phone calls | Integration BUILT and tested against a fake LiveKit: plan, status, apply, teardown, caller detection, optional hang-up. NO REAL INBOUND CALL YET: blocked on secure trunk authentication (see below) |
| 3 | Real outgoing Indian phone calls triggered through Django | **Website-triggered outbound calling PASS (16 Nov 2026):** staff page, live status, call records and worker reports verified on two real calls. Limited to the owner's allow-listed number; consent handling and calls to anyone else not started |
| 4 | PostgreSQL-backed Django dashboard, call records, transcripts, appointment booking and agent management | Started: call-record model in use on real calls, verified on PostgreSQL 17 (the local database is still SQLite). Transcripts, booking, agent management and a full dashboard not started |
| 5 | Production security, performance verification and deployment | Not started |

The voice worker and Django remain separate processes. Automated suite: 265 tests.
Provider: Vobiz (the owner is an individual without GST, which rules out Plivo).

## Completed real-call checkpoints

| Date | Call | Result |
| :--- | :--- | :--- |
| 14 Nov 2026, 13:06 | First outbound call, 99 s | Connected with TLS, required media encryption and India routing. Two-way Telugu conversation worked. Problems: replies paused mid-sentence for up to 3.5 s, formal Telugu, one stock question repeated, English answered in Telugu, no hang-up |
| 14 Nov 2026, 16:23 | Second outbound call, 109 s | **Voice-quality checkpoint PASS (owner-confirmed):** natural Telugu-English, no broken audio, English answered in English, deliberate interruption honoured, AI hang-up worked. Event log: 0 pauses inside replies, 2 interruptions (both genuine), reply latency median 1.37 s |
| 16 Nov 2026, 09:53 | First call from the Django page, 56 s | Completed. 5 turns each way, 1 interruption, median reply latency 1.31 s; outcome and metrics reported by the worker |
| 16 Nov 2026, 10:07 | Website-triggered acceptance call, 67 s | **PASS.** Answered after 11 s of ringing, completed, record matches the worker's own logs (6 turns each way, 2 interruptions, median latency 1.46 s, worst 2.15 s); no stuck or duplicate records; LiveKit room cleaned up |

Between the first two calls the prompt, style sheet and greeting were rewritten, the hang-up
tool was enabled, and timing instrumentation was added. No audio, interruption, TTS,
voice or Vobiz setting was changed. Voice remains `kavitha`.

## Known open issue

**Occasional pause inside a reply: cause now confirmed (16 Nov 2026).** On the
acceptance call the agent started a reply, 0.09 s later the line carried 0.5 s of sound
from the caller's side (likely echo or the end of the caller's sentence), which met the
0.5 s interruption threshold. No words followed, so it was a false interruption and the
agent resumed after LiveKit's default 2.0 s wait: one 2.0 s silence. This matches the
first call's longer pauses. Sarvam TTS and the SIP media path are not involved.

The wait is now configurable as `INTERRUPTION_FALSE_TIMEOUT` (default unchanged at
2.0). The suggested next experiment, one change at a time with barge-in kept on, is
1.0 s on the next call, then compare with `call_report.py`. Not applied: the owner asked
to keep audio settings unchanged.

```powershell
.\.venv\Scripts\python.exe scripts\call_report.py
```

If it recurs, the report shows each pause and what preceded it; change one interruption
setting at a time and keep barge-in on.

## Voice quality: not production-ready

Owner's rating of the 16 Nov calls with a 1.0 s false-interruption wait: **poor**.
The logs showed the agent could rarely finish a sentence. Status of each cause:

| Cause | Status |
| :--- | :--- |
| No noise cancellation on phone calls; background speech muted the agent for 26 s | Fixed in code (same cancellation as the browser studio); **needs one real call to verify** |
| Greeting cut off by the callee's "hello" | Fixed: the phone greeting cannot be interrupted |
| A lone "ఆ" or "ఊ" discards the agent's answer | Built, **off**: `INTERRUPTION_IGNORE_ACKS`. Holds back acknowledgements only; "ఆగండి", "stop", "bye" and any other word still interrupt. Enable after the noise-cancellation call, as one change |
| Agent had no purpose and kept asking how to help | Fixed: agent profile (purpose, goals, limits, business information) edited at `/agent/` |
| "office people", re-asking after acknowledgements | Fixed in the prompt; scenarios pass |
| Voice naturalness, pronunciation, phone audio quality | Unknown: needs the owner's ears and a recording |
| False-interruption wait | 1.0 s kept provisionally |

`INTERRUPTION_MIN_WORDS` stays 0 by owner decision: a word minimum would block
one-word commands.

Not yet possible: the agent cannot save what the caller tells it (for example a
preferred appointment time). Until call outcomes are stored, that exists only in the
worker log.

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
| Trigger a call from the Django web application | **Done and verified on real calls** (16 Nov 2026) |
| Call records: who, when, outcome, duration | Done: masked number, ringing/answer/end times, talk time, SIP status, latency; web and command-line calls |
| More than one allowed number, with consent recorded | Not started. DLT and consent rules must be settled before any call to third parties |
| Noise cancellation on the outbound path | Not applied yet |
| Caller ID shown as "Spam Alert" by Jio on the trial number | Seen on the first call's recording; revisit with a purchased number |
| Call that no worker picks up | Fixed 16 Nov: withdrawn after 15 s; the worker also refuses requests older than 60 s, so a late worker cannot ring the phone |
| Worker must be started separately from Django | Operational: start it first and wait for `registered worker` |

### Dashboard and data (Phase 4)

| Item | Status |
| :--- | :--- |
| Call records and recent-calls table | Done (outbound) |
| Switch the local database to PostgreSQL | Ready (`compose.yaml`); owner to switch |
| Call detail page: timeline, metrics, events | Not started |
| Transcripts per call, with a retention policy and consent notice | Not started |
| Appointment booking (tools the agent can call, with confirmation) | Not started |
| Agent settings: purpose, greeting, goals, limits, business information | Done (`/agent/`), one active profile; voice selection and multiple profiles not started |
| Save call outcomes the agent collects (preferred time, yes/no) to the call record | Not started; next for the appointment agent |
| Reports: calls per day, outcomes, latency trends | Not started |
| Contact list with recorded consent, before calling anyone but the owner | Not started |

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
| First real call placed from the page | **PASS** on two real calls (16 Nov 2026) |

## Next milestone

**Secure inbound calls into the same records and page**, once LiveKit enables
allow-list enforcement or another verified authentication method is available.
In parallel: switch the local database to PostgreSQL, and decide the consent and DLT
rules that must exist before the agent calls anyone other than the owner.
