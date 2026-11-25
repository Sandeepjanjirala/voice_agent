# Phase 1.5 — Django voice studio

> **Removed on 25 November 2026 at the owner's request.** The browser voice
> studio and its token API are gone; this page is kept as history. To hear an AI
> employee, call your own number from Make calls.

**Local functional acceptance: PASS, with incomplete extended checks below.**
Verified locally on Windows on 13 November 2026; updated after owner browser testing. The existing worker, model settings,
prompts, scenarios and .env.local were not changed.

## Implementation

- Django templates, local CSS and lightweight JavaScript at `/`, protected by Django login.
- Vendored official `livekit-client` 2.24.0 UMD bundle, with its Apache-2.0 license.
  Source: https://unpkg.com/livekit-client@2.24.0/dist/livekit-client.umd.js
  No CDN request or Node build is required to run the application.
- POST `/api/voice/token/`: authenticated active user, CSRF validation, no-store response,
  five-minute JWT, server-generated opaque room/participant IDs. Microphone publishing
  and subscription only; no camera, data publishing, room-admin or SIP grants.
  Room configuration dispatches the configured `AGENT_NAME` (existing `telugu-agent`).
- API secrets remain in Django settings. No tokens in URLs, browser storage or application logs.
- Start/end, microphone permission messages, mute/unmute, audio autoplay recovery,
  connecting/listening/thinking/speaking indicators, SDK reconnect handling, bounded
  startup/recovery waits and cleanup when permission arrives after cancellation.
- `lk.transcription` streams update user/agent transcript rows using segment IDs;
  final text replaces interim text. Text is inserted with `textContent`.
- No business persistence or worker behavior changes.

## Evidence and limits

| Check | Result |
| :--- | :--- |
| Original unit-test baseline | 74 passed using project-local pytest temporary directory |
| Expanded Python suite | 92 passed; 18 view/token checks plus original 74 |
| Django system check | No issues |
| Real Chromium + Django | Passed login, official SDK loading, authenticated token endpoint, desktop/mobile layout |
| Chromium with simulated transport | Passed permission denial, state updates, streamed transcript deduplication, safe text rendering, mute/unmute, reconnect events, audio unlock, end/cancel cleanup, connection error and disconnect |
| Real Chromium + LiveKit Cloud + existing worker | Passed room connection, dispatch, Telugu greeting transcript, remote audio element playback, microphone mute and end cleanup |
| Live smoke input | Chromium synthetic microphone; no human speech accuracy or audible naturalness claim |
| Prior simulations | Existing five successful simulations preserved; not rerun because worker behavior and scenarios were unchanged |
| Human microphone conversation through new Django UI | Owner-confirmed pass: Telugu and mixed Telugu-English |
| Temporary network interruption | Owner-confirmed recovery; duration and mechanism unspecified |
| Controlled physical outage, Safari/Firefox, concurrent live users | Not independently tested |

Ruff lint and formatting passed (45 Python files). Two existing warnings concern short
dummy JWT keys in older voice pipeline tests; the final run also reported a harmless
pytest-cache write permission warning. No real credential was printed.
Screenshots are local ignored QA artifacts under
`browser-artifacts/`, not session recordings.

The first baseline attempts hit Windows temporary-directory access and then a missing
parent directory. Creating `.test-tmp` and using `--basetemp` resolved this without
changing existing tests. The known disappearing Ruff launcher required restoration
from the local uv cache.

A back-to-back browser rerun hit the intended token throttle (HTTP 429) because both
suites used the same QA account. The live test now waits for that cooldown; its rerun
passed without changing the application throttle.

Simulation CI remains configured on pushes/merges to main; its command now includes
`--concurrency 1` to match the known reliable local invocation. CI was not executed here.

## Run locally (no global uv required)

From `C:\Users\Sandeep.J\Documents\agent`, with the existing `.env.local`:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Reuse an existing Django account instead of creating another if you already have one.
In a separate PowerShell window, start the existing worker (skip if already running):

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m voice_agent.agent dev
```

Open http://127.0.0.1:8000/, sign in, choose Start Conversation, and allow the microphone.
Use headphones. End Conversation releases browser capture and leaves the room;
LiveKit's existing worker lifecycle handles departure. Token expiry limits joining,
not the duration of a connected conversation. Use HTTPS outside localhost.

## Repeat checks

```powershell
New-Item -ItemType Directory -Force .test-tmp | Out-Null
.\.venv\Scripts\python.exe -m pytest --basetemp=.test-tmp/check
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe manage.py check
```

Optional browser QA requires Node and Playwright (only for testing):

```powershell
npm.cmd install --no-save --package-lock=false playwright@1.49.1
npx.cmd playwright install chromium
.\.venv\Scripts\python.exe -m tests.browser.server
# In another terminal:
node tests/browser/voice.cjs
# Opt-in live service use: requires the existing worker and incurs inference usage.
node tests/browser/live_smoke.cjs
```

The QA server binds only to loopback, uses `.test-tmp/browser.sqlite3` and a disposable
test account, and must never be used for normal operation or deployment. Stop it
after testing. Normal runserver uses your own account, never the QA account.

## Remaining review

Owner reports all ten manual checklist items working: login, Start, audible greeting,
Telugu comprehension, mixed speech, interruption, mute/unmute, End releasing the mic,
new conversation and temporary-network recovery. This observation concerns the owner's
normal flow; the edge-case fixes below were independently tested afterward.

Naturalness ratings, pauses, short-reply accuracy, backchannels/noise, turn-mode
comparisons and measured/repeated outage recovery remain incomplete. Firefox/Safari,
multiple concurrent live users and production security/performance are not certified.

The five-second token throttle uses Django's local cache and is only a local abuse
guard. Shared rate limits, login hardening, HTTPS/cookies, session duration/cost limits,
retention policy and concurrency/load verification remain Phase 5 work. This is not
a production security or performance certification.

## API references checked

- https://docs.livekit.io/frontends/reference/tokens-grants/
- https://docs.livekit.io/frontends/build/agent-state/
- https://docs.livekit.io/agents/multimodality/text/
- https://docs.livekit.io/transport/data/text-streams/

Python signatures verified against installed `livekit-api` 1.2.1; browser bundle
verified in Chromium. The SDK's text-stream interface is used rather than deprecated
`TranscriptionReceived` events.

## Final review, confirmed fixes and security audit

Three browser edge cases were reproduced in Chromium with deterministic SDK events
before fixing them. These are regression reproductions, not claims about the owner's
successful manual sessions:

1. `SignalReconnecting` left the UI showing Speaking instead of recovery. Both signaling
   and media reconnect events now enter the bounded reconnect state.
2. Local microphone unpublishing (and participant removal) during SDK recovery was
   treated as terminal. Recovery now tolerates that transition; normal out-of-recovery
   loss still ends the session, with the recovery timeout bounding failures.
3. End during a pending unmute could leave reacquired capture running after cleanup.
   The completed async operation now stops its track if its session has already ended.
   A stale playback-error callback is also prevented from changing a later session's UI.

All fixes are in browser JavaScript. No voice worker changes or new agent were required.
SDK behavior was checked against `livekit-client` v2.24.0 sources (RoomEvent,
LocalParticipant.republishAllTracks, LocalAudioTrack.unmute). Browser regression coverage
includes the previously failing paths plus existing transcript/XSS, controls and errors.
The post-fix real LiveKit check used the owner's already-running worker and synthetic
Chromium microphone: greeting transcript/audio, actual SDK mute/unmute, and underlying
MediaStreamTrack `readyState=ended` after End all passed. No audio-quality judgment.
The temporary QA server was stopped; the owner's worker was left running.

Security tests pass for active-user authentication, inactive-user denial, wrong passwords,
external login-redirect rejection, login/token CSRF, POST-only minting/logout, isolated
rooms/identities, five-minute expiry, signature-tampering/expiry rejection by JWT
verification, minimal microphone grants, server-owned dispatch, throttle, no-store,
no secret in response/template, HttpOnly session cookies, SameSite=Lax, frame denial,
MIME sniffing prevention and same-origin referrer policy. There is no wildcard CORS.
JWT verification tests are local; an expired token was not submitted to LiveKit Cloud.
Tokens are bearer credentials: logout does not revoke an already-issued LiveKit token
or disconnect other tabs; expiry limits joining, not connected-session duration.

`manage.py check`: PASS (zero issues). `manage.py check --deploy`: NOT production-ready,
with six warnings against the local configuration:

| Warning | Finding |
| :--- | :--- |
| security.W004 | HSTS not configured |
| security.W008 | HTTPS redirect not enabled |
| security.W009 | Current Django secret does not meet deployment strength checks |
| security.W012 | Session cookie not HTTPS-only |
| security.W016 | CSRF cookie not HTTPS-only |
| security.W018 | DEBUG enabled |

No secret values were displayed or rewritten. HTTPS-only settings are not forced on
localhost HTTP. Secret rotation, HTTPS/cookies, shared throttling/login protections and
session revocation/lifetime policies remain production-readiness work in Phase 5.
This local functional pass must not be used as deployment approval.

Final automated run: **92 passed**, Ruff lint passed, Ruff formatted 45 Python files,
and Django check passed. Warnings: two older voice tests use short dummy JWT keys;
pytest could not persist its optional cache on this Windows machine. Neither failed
the tests. A first security-test assertion mistakenly used force_login's synthetic
cookie; it was corrected to inspect the actual login response, which is HttpOnly.
The five earlier live simulations are preserved and were not rerun: agent behavior
and scenarios were unchanged. GitHub Actions was not run here.

**Phase 1.5 local functional result: PASS. Extended/production checks: INCOMPLETE.**
No Phase 2 implementation, telephony purchase, commit, push, merge or deployment.
