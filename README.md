# Telugu AI Calling Agent

A voice agent that converses in Telugu, English and mixed Telugu-English, built for
inbound and outbound calls on Indian (+91) numbers.

**Status: real outbound calls work; the web calling page is built and awaits its
first real call.** Two outbound test calls to the owner's mobile succeeded through
Vobiz. Staff can now place that call from the Django web application, follow it live,
and see a database record for every call. Inbound calling is built but blocked on
secure trunk authentication. Nothing is deployed. See [docs/ROADMAP.md](docs/ROADMAP.md).

| Document | What it covers |
| :--- | :--- |
| [docs/PROGRESS.md](docs/PROGRESS.md) | Dashboard build: what each milestone delivered and how it was checked |
| [docs/FEATURE_MATRIX.md](docs/FEATURE_MATRIX.md) | Feature-by-feature comparison with the reference product, and what is still missing |
| [docs/CONTACT_KEYS.md](docs/CONTACT_KEYS.md) | Backing up, recovering and rotating the keys that protect stored phone numbers |
| [docs/INBOUND.md](docs/INBOUND.md) | Inbound routing, call lifecycle, and the transfer design |
| [docs/OUTBOUND_TEST.md](docs/OUTBOUND_TEST.md) | The first real call: one outbound test call to your own mobile |
| [docs/VOBIZ_SETUP.md](docs/VOBIZ_SETUP.md) | Step-by-step account, number and trunk setup for the first real inbound call |
| [docs/PHASE2_IMPLEMENTATION.md](docs/PHASE2_IMPLEMENTATION.md) | Inbound telephony integration: what was built, verified, and what is not yet proven |
| [docs/PHASE2_READINESS.md](docs/PHASE2_READINESS.md) | Provider comparison, costs, regulation and the Phase 2 plan |
| [docs/CALL_TERMINATION_DESIGN.md](docs/CALL_TERMINATION_DESIGN.md) | Goodbye and hang-up behaviour, off unless `AGENT_CAN_END_CALL=true` |
| [docs/PHASE1_5_VERIFICATION.md](docs/PHASE1_5_VERIFICATION.md) | Run the Django voice studio and review browser verification |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Full incoming/outgoing calling roadmap, Phases 1?5 |
| [docs/AUDIT.md](docs/AUDIT.md) | What the implementation brief got right and wrong against current SDKs |
| [docs/PHASE1_VERIFICATION.md](docs/PHASE1_VERIFICATION.md) | What was tested, what was not, and the microphone tests you need to run |
| [docs/adr/0001-two-process-boundary.md](docs/adr/0001-two-process-boundary.md) | Why the voice worker and Django are separate processes |

## How it is put together

Two processes that are started and deployed separately:

| Process | Code | Does |
| :--- | :--- | :--- |
| Voice worker | `voice_agent/` | LiveKit Agents: Sarvam STT, LLM, Sarvam TTS, turn-taking |
| Django backend | `config/`, `apps/` | Django + DRF: authenticated voice studio, secure room tokens and health API; business dashboard later |

The worker never imports Django and Django never imports the voice stack. A test
enforces this.

## Setup on Windows

Run these in PowerShell from the repository folder.

**1. Install the tools** (once per machine):

```powershell
winget install astral-sh.uv
winget install LiveKit.LiveKitCLI
```

Close and reopen PowerShell so `uv` and `lk` are on your PATH, then check. `lk agent`
commands run the worker through `uv`, so `uv` must be on PATH: without it they fail
with "livekit-agents is not installed in the project environment".

```powershell
uv --version      # 0.13 or later
lk --version      # 2.19 or later
```

**2. Install dependencies.** uv downloads Python 3.12 if needed and creates `.venv`:

```powershell
uv sync
```

**3. Create your settings file:**

```powershell
Copy-Item .env.example .env.local
```

**4. Connect LiveKit Cloud.** Create a free project at <https://cloud.livekit.io>, then:

```powershell
lk cloud auth
lk app env --write --destination .env.local
```

This opens a browser to sign in and writes `LIVEKIT_URL`, `LIVEKIT_API_KEY` and
`LIVEKIT_API_SECRET` into `.env.local`.

**5. Add your Sarvam key.** Get one at <https://dashboard.sarvam.ai>, open `.env.local`
and set `SARVAM_API_KEY=`.

That is everything the default configuration needs. The LLM runs through LiveKit
Inference on your LiveKit project, so no OpenAI key is required. To use OpenAI
directly, set `LLM_PROVIDER=openai` and `OPENAI_API_KEY`.

**6. Make the terminal Telugu-safe.** Use Windows Terminal, and in each new session:

```powershell
$env:PYTHONUTF8 = "1"
```

Without this, Telugu text in logs can fail to print in older consoles.

## Run the voice worker

Talk to it with your microphone and speakers (headphones recommended):

```powershell
lk agent console voice_agent/agent.py
```

Keys while it runs: `m` mutes the microphone, `Ctrl+T` switches to typing, `q` quits.
Add `--record` to save the audio under `console-recordings/`, `--text` to start in
typing mode, and `--list-devices` to see audio devices.

Other ways to run it:

```powershell
lk agent dev voice_agent/agent.py            # connect to LiveKit Cloud, hot reload
lk agent debugger start voice_agent/agent.py # text turns from a script; then:
lk agent debugger say "నమస్తే, నాకు appointment కావాలి"
lk agent debugger stop
uv run voice_agent/agent.py start            # production mode
```

After a conversation, summarise the measured latency:

```powershell
uv run python scripts/latency_report.py
```

## Run your Django voice studio

Use the existing virtual environment directly; global `uv` is not needed to run it.
In PowerShell from this repository:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Skip `createsuperuser` if you already have a Django account. In another window,
start the existing worker if it is not already connected:

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m voice_agent.agent dev
```

Open <http://127.0.0.1:8000/>, sign in, choose **Start Conversation**, and allow the
microphone. Use headphones. **End Conversation** stops capture and disconnects.
The page shows user and agent transcripts and the assistant's current state.
Microphone access requires localhost or HTTPS. No React, build step or CDN is needed.

Both processes use the existing `.env.local`. Django needs `LIVEKIT_URL`,
`LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, and the same `AGENT_NAME` as the worker.
API secrets stay on the server. Each start creates an isolated room with a
five-minute microphone-only join token and dispatches `telugu-agent`.

The health API remains at <http://127.0.0.1:8000/api/health/>.
Django's existing SQLite fallback stores login/session data locally. PostgreSQL and
business records remain Phase 4. See [the verification and testing guide](docs/PHASE1_5_VERIFICATION.md)
for exact browser coverage and pending human checks.

## Dashboard and AI employees (staff only)

Signing in as a staff user opens <http://127.0.0.1:8000/home/>: your AI employees,
this week's calls and a setup checklist. **Hire an employee** takes a job described
in plain language and drafts an editable employee: name, opening line, steps,
details to collect, limits and ending. Edit it, press **Try the draft** to talk to
it in the Voice studio, then **Publish**. Each publish is a numbered version; older
versions can be copied back into the draft. **Call the test number** on a published
employee places one real call to the approved test number with that version. Sections marked *Planned* in the sidebar open a page that
says what works today and what is still to come.

Drafting uses the same `LLM_*` settings as the voice worker, so it needs no extra
key. If the LLM cannot be reached, the draft starts from a plain template and says so.

### Organisations, campuses and roles

| Role | Sees and does |
| :--- | :--- |
| Platform superuser (Django superuser) | Creates organisations; works in one at a time, chosen in the sidebar |
| Organisation admin | Everything in their organisation: campuses, staff, employees, leads, reports, access log, retention |
| Campus staff | Leads, contacts, calls and reports of the campuses ticked for them |

On first sign-in the superuser is asked to set up an organisation; existing
employees and call records move into it. Add staff under **Campuses & staff**.
A record in another organisation or campus is a 404, whatever ID is typed.

### Contacts and phone numbers

Contact numbers are encrypted before they reach the database, matched through a
keyed hash, and shown masked. Create the two keys once and add them to `.env.local`:

```powershell
.\.venv\Scripts\python.exe manage.py contactkeys generate   # prints the two lines
.\.venv\Scripts\python.exe manage.py contactkeys status
.\.venv\Scripts\python.exe manage.py contactkeys rotate     # after putting a new key first
.\.venv\Scripts\python.exe manage.py retention              # preview; add --apply to carry out
```

Keep the keys out of Git and out of database backups; in production use a secrets
manager. Calling stored contacts is switched off (`CONTACT_DIALING_ENABLED` in
`config/settings.py`): calls go only to `OUTBOUND_TEST_ALLOWED_NUMBER`. Use made-up
contacts until real data is authorised.

### Calling channels, campaigns and results

- **Numbers & channels**: business lines, each with a campus and the published
  employee that answers it. Inbound calls stay off until the SIP trunk can be
  authenticated; see [docs/INBOUND.md](docs/INBOUND.md).
- **Campaigns** and **Instant leads** run as dry runs: they show who would be
  called and when, and dial nobody. Instant leads arrive at
  `POST /api/intake/<source id>/` with the source's `X-Lead-Secret`.
- **Knowledge**: facts and rules per campus, given to each call.
- After a call, **Analyse now** (or `manage.py analyse_calls --apply` from a
  scheduled task) writes the summary and carries out the employee's permitted
  actions. It needs transcripts, which are off until chosen under Privacy &
  settings.

## Inbound telephony

These commands buy nothing and place no call. The first three change nothing:

```powershell
.\.venv\Scripts\python.exe manage.py telephony plan              # no network
.\.venv\Scripts\python.exe manage.py telephony status            # lists LiveKit SIP config
.\.venv\Scripts\python.exe manage.py telephony apply --dry-run   # what apply would create
.\.venv\Scripts\python.exe manage.py telephony apply --confirm-number +91XXXXXXXXXX --yes-change-livekit
```

`plan` shows the inbound trunk and dispatch rule that would be created and every
blocker in the way. Unverified provider or LiveKit capabilities are blockers until you
list them in `TELEPHONY_CONFIRMED`. `apply` creates the LiveKit trunk and dispatch rule
only when the plan has no blockers; `teardown` removes them. Settings are under
"Inbound telephony" in [.env.example](.env.example). Follow
[docs/VOBIZ_SETUP.md](docs/VOBIZ_SETUP.md) for the first real call.

## Phone calls page (outbound, staff only)

Staff users get a **Phone calls** link next to Voice studio. The page calls only
`OUTBOUND_TEST_ALLOWED_NUMBER`, one call at a time, after you press **Call** and
confirm. It shows the call live (initiating, ringing, answered, then completed,
rejected, no answer or failed), lets you end it, and lists recent calls. Every call,
including `manage.py telephony call`, is stored as a record with a masked number,
timestamps, talk time, SIP status and latency.

Make your account staff once (or use a superuser):

```powershell
.\.venv\Scripts\python.exe manage.py shell -c "from django.contrib.auth import get_user_model as U; U().objects.filter(username='YOUR_USERNAME').update(is_staff=True)"
```

Run it, in three PowerShell windows from this folder:

```powershell
# 1. Django
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000

# 2. Voice worker (restart it after changing .env.local)
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m voice_agent.agent dev
```

3. Open <http://127.0.0.1:8000/calls/> and sign in.

`CALL_EVENTS_URL` and `CALL_EVENTS_SECRET` in `.env.local` let the worker report each
call's outcome, SIP status and latency to Django (signed; no numbers or transcripts).
Without them the page still works and infers outcomes from LiveKit, with less detail.

### PostgreSQL

SQLite remains the zero-setup default. To use PostgreSQL locally with Docker Desktop:

```powershell
# In .env.local: POSTGRES_PASSWORD=<a strong password>
docker compose --env-file .env.local up -d
# In .env.local: DATABASE_URL=postgres://telugu_agent:<that password>@127.0.0.1:5432/telugu_agent
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
```

Accounts do not move between databases, so create your user again after switching.

## Change the voice, model or language

Edit `.env.local` and restart the worker. Every setting is described in
[.env.example](.env.example). The ones you are most likely to change:

| Setting | Default | Meaning |
| :--- | :--- | :--- |
| `SARVAM_TTS_SPEAKER` | `kavitha` | Voice. An invalid name is rejected at startup with the valid list |
| `SARVAM_TTS_PACE` | `1.0` | Speaking speed, 0.3 to 3.0 |
| `SARVAM_STT_LANGUAGE` | `te-IN` | `auto` detects the language per utterance |
| `SARVAM_STT_TASK` | `codemix` | Keeps English words in Latin letters inside Telugu text |
| `SARVAM_STT_MODE` | `standard` | `realtime` uses `saaras:v3-realtime` (needs a Sarvam realtime plan) |
| `LLM_PROVIDER` / `LLM_MODEL` | `livekit` / `google/gemma-4-31b-it` | Which LLM answers |
| `TURN_DETECTION` | `vad` | How the agent decides you have finished speaking |
| `TURN_ENDPOINTING_MIN_DELAY` | `0.5` | Seconds of silence before it replies |
| `AGENT_GREETING` | Telugu greeting | First thing the agent says |

The prompt is in `voice_agent/prompts/__init__.py` and the Telugu style sheet in
`voice_agent/grammar/te_style.md`. Together they must stay under 2.3 kB, and the
per-call agent profile section under 1 kB; tests check both.

## Tests and linting

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format .
```

The tests need no API keys and no network.

Conversation scenarios in `scenarios.yaml` run on LiveKit Cloud and use real inference:

```powershell
lk agent simulate text --scenarios scenarios.yaml --concurrency 1 voice_agent/agent.py
```

`--concurrency 1` is needed on the Windows development machine, where parallel local
sessions failed to connect in time. To compare LLMs on the agent's prompt:

```powershell
uv run python scripts/llm_bakeoff.py google/gemma-4-31b-it openai/gpt-5.4-mini
```

### If `pytest` or `ruff` stop working on this machine

On the development machine, `pytest.exe` and `ruff.exe` were removed from
`.venv\Scripts` shortly after being run, most likely by security software.
Use `uv run python -m pytest` (as above), which is not affected. If ruff reports
"program not found", restore it with:

```powershell
uv sync --reinstall-package ruff
```

Ask IT to exclude the project's `.venv` folder if this keeps happening.

### Tests for the phone calls page

```powershell
# Python: unit, API and cross-process tests (no network, no calls)
.\.venv\Scripts\python.exe -m pytest

# Same suite against PostgreSQL (needs the compose database running)
$env:TEST_DATABASE_URL = "postgres://telugu_agent:<password>@127.0.0.1:5432/telugu_agent"
.\.venv\Scripts\python.exe -m pytest
Remove-Item Env:TEST_DATABASE_URL

# Dashboard and Hire wizard in Chromium (canned LLM answer; restart the server per run)
.\.venv\Scripts\python.exe -m tests.browser.dashboard_server   # window 1
node tests/browser/dashboard.cjs                               # window 2
node tests/browser/crm.cjs                                     # window 2, same server
node tests/browser/calling.cjs                                 # after restarting the server

# Browser: real Chromium and Django, simulated LiveKit, fake numbers
.\.venv\Scripts\python.exe -m tests.browser.calls_server   # window 1; Ctrl+C when done
node tests/browser/calls.cjs                                 # window 2
```

The browser QA server sets fake numbers and credentials before Django starts, so it
cannot place a real call. It uses a disposable database under `.test-tmp/`.

## Troubleshooting

| Symptom | Cause and fix |
| :--- | :--- |
| `Voice agent configuration error: SARVAM_API_KEY is not set` | `.env.local` is missing or the key is blank |
| `ws_url is required, or set LIVEKIT_URL` | Step 4 was skipped |
| `SARVAM_TTS_SPEAKER=... is not a bulbul:v3 voice` | Pick a name from the list in the message |
| Agent replies "క్షమించండి, ప్రస్తుతం చిన్న సాంకేతిక సమస్య ఉంది" | The LLM or STT call failed; the log line `pipeline error` has the reason (often an invalid key or no credit) |
| Agent cuts you off while you think | Raise `TURN_ENDPOINTING_MIN_DELAY`, for example to `0.8` |
| Agent stops talking at background noise | Use headphones; raise `INTERRUPTION_MIN_DURATION` |
| `STTRealtime` fails to connect | Your Sarvam plan lacks realtime access; keep `SARVAM_STT_MODE=standard` |
| `lk` or `uv` not recognised | Reopen PowerShell after installing |
| `livekit-agents is not installed in the project environment` | `uv` is not on PATH; `winget install astral-sh.uv` and reopen PowerShell |
| Simulations fail with `No agent joined room within 30s` | Add `--concurrency 1` |

## Project layout

```text
voice_agent/          LiveKit worker (separate process)
  agent.py            entrypoint
  assistant.py        the agent: instructions and greeting
  config.py           environment -> validated settings
  pipeline.py         builds STT, LLM, TTS and turn handling
  observability.py    logging, per-turn latency, spoken error fallback
  prompts/  grammar/  system prompt and Telugu style sheet
config/               Django project (settings, urls, asgi)
apps/core/            health API, voice views, templates and static assets
apps/telephony/       telephony: trunk planning, outbound calls, call records, calls page
apps/workforce/       AI employees: hire wizard, LLM drafting, draft editor, dashboard home
apps/tenancy/         organisations, campuses, roles and scope, access log
apps/crm/             encrypted contacts, leads, follow-ups, import, reports, retention
apps/calling/         numbers, inbound routing, campaigns, instant leads, call results
scenarios/            proposed scenarios that are not yet run in CI
scripts/              latency_report.py, llm_bakeoff.py
tests/                django/, voice/, test_process_boundary.py
scenarios.yaml        LiveKit simulation scenarios
docs/                 audit, verification report, ADR
```

## Credits

The worker scaffolding (project layout conventions, `Dockerfile`, CI workflows,
`AGENTS.md`) is adapted from
[livekit-examples/agent-starter-python](https://github.com/livekit-examples/agent-starter-python)
(MIT, Copyright LiveKit, Inc.).
