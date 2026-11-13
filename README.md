# Telugu AI Calling Agent

A voice agent that converses in Telugu, English and mixed Telugu-English, built for
inbound and outbound calls on Indian (+91) numbers.

**Status: Phase 1, local prototype.** The voice loop runs against your microphone
and has been verified against live LiveKit and Sarvam services with synthetic speech;
human microphone tests are pending (see the verification report).
There is no telephony, database schema, dashboard or deployment yet.

| Document | What it covers |
| :--- | :--- |
| [docs/AUDIT.md](docs/AUDIT.md) | What the implementation brief got right and wrong against current SDKs |
| [docs/PHASE1_VERIFICATION.md](docs/PHASE1_VERIFICATION.md) | What was tested, what was not, and the microphone tests you need to run |
| [docs/adr/0001-two-process-boundary.md](docs/adr/0001-two-process-boundary.md) | Why the voice worker and Django are separate processes |

## How it is put together

Two processes that are started and deployed separately:

| Process | Code | Does |
| :--- | :--- | :--- |
| Voice worker | `voice_agent/` | LiveKit Agents: Sarvam STT, LLM, Sarvam TTS, turn-taking |
| Django backend | `config/`, `apps/` | Django + DRF: a health endpoint for now; data, auth and dashboard later |

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

## Run the Django backend

In a second PowerShell window:

```powershell
uv run python manage.py migrate
uv run python manage.py runserver
```

Then open <http://127.0.0.1:8000/api/health/>. Expected response:

```json
{"status": "ok", "service": "django", "database": "ok"}
```

`.env.example` ships with `DJANGO_DEBUG=true`, which lets Django start without a secret
key and use a local SQLite file. Set `DATABASE_URL=postgres://...` to use PostgreSQL;
it becomes mandatory in Phase 4.

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
`voice_agent/grammar/te_style.md`. Together they must stay under 2 kB; a test checks it.

## Tests and linting

```powershell
uv run python -m pytest
uv run ruff check .
uv run ruff format .
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
apps/core/            health endpoint
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
