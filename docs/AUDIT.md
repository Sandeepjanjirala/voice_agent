# Phase 0 audit: brief vs. current code and docs

Date: 13 November 2026. Scope: `IMPLEMENTATION_BRIEF_DJANGO.md`, the four referenced
repositories (cloned and read), the current LiveKit docs, and the SDKs as actually
installed on this machine. Nothing here was tested against a live phone line.

## Verdict

The two-process architecture in the brief is sound and is what was built. The brief's
SDK details are mostly right for LiveKit Agents 1.8, but eight of its assumptions are
wrong or out of date. Two of them changed the Phase 1 design (items 1 and 2).

## What was inspected

| Source | Version inspected | Licence |
| :--- | :--- | :--- |
| `livekit-examples/agent-starter-python` | commit `228095c`, 2026-11-04 | MIT |
| `DINAKAR-S/voice-agent-starter-kit` | commit `46cad98`, 2026-08-31 | MIT |
| `vobiz-ai/Livekit-vobiz-inbound` | commit `bab69fd`, 2026-11-03 | MIT |
| `vobiz-ai/LiveKit-Vobiz-Outbound` | commit `a266484`, 2026-11-03 | MIT |
| LiveKit docs | Sarvam STT, Sarvam TTS, turn detector, adaptive interruptions, simulations | n/a |

Installed and locked in `uv.lock`:

| Package | Version | | Package | Version |
| :--- | :--- | :--- | :--- | :--- |
| livekit-agents | 1.8.10 | | Django | 5.2.19 |
| livekit-plugins-sarvam | 1.8.10 | | djangorestframework | 3.18.3 |
| livekit-plugins-openai | 1.8.10 | | psycopg (binary) | 3.3.9 |
| livekit-plugins-ai-coustics | 0.3.5 | | dj-database-url | 3.1.2 |
| livekit-api | 1.2.1 | | pytest / pytest-django | 9.2.0 / 4.14.0 |
| livekit (rtc) | 1.1.21 | | ruff | 0.17.5 |

Tools: Python 3.12.10, uv 0.13.8, LiveKit CLI `lk` 2.19.3.

## Findings that changed the design

**1. The LiveKit turn detector does not support Telugu.** The brief says to "leverage
LiveKit's Turn Detector". Its documented languages are English, Arabic, German, Spanish,
French, Hindi, Indonesian, Italian, Japanese, Korean, Dutch, Portuguese, Turkish and
Chinese. The starter enables it by default.
*Decision:* the default is pause-based endpointing (`TURN_DETECTION=vad`), with
`stt` (Sarvam's own end-of-speech signal) and `livekit` available to compare on a
microphone. Which one is best for Telugu is an open question until it is measured.

**2. `sarvam.STT` is not a non-streaming "legacy" API.** In the installed plugin,
`sarvam.STT` (`saaras:v4`) is a streaming WebSocket client with interim results,
server-side speech start/end signals and its own reconnect logic. `sarvam.STTRealtime`
(`saaras:v3-realtime`) is confirmed to never reconnect (`max_retry=0`, "bills per
connection") and needs a Sarvam plan with realtime access.
*Decision:* the default is `sarvam.STT` + `saaras:v4`. When `realtime` is selected it is
wrapped in `stt.FallbackAdapter([STTRealtime, STT])`, so a dropped realtime socket falls
over to the reconnecting API instead of ending the call. This replaces the brief's
hand-written "session supervisor".

## Other incorrect or outdated assumptions

| # | Brief says | What is true now | Action |
| :--- | :--- | :--- | :--- |
| 3 | `language="auto"` works on both STT classes | `auto` is `STTRealtime` only; `sarvam.STT` uses `unknown` | Config accepts either spelling and maps it |
| 4 | One `SARVAM_STT_MODE` holds both the API choice and the task | They are separate parameters | Split into `SARVAM_STT_MODE` and `SARVAM_STT_TASK` |
| 5 | Noise cancellation is the "Krisp plugin" | The starter now uses `livekit-plugins-ai-coustics` on the agent side; `krisp_enabled` remains the flag on SIP trunks and participants | ai-coustics behind `NOISE_CANCELLATION`; Krisp flag is Phase 2 |
| 6 | Latency comes from the metrics event (as in DINAKAR-S) | `metrics_collected` is deprecated in 1.8; per-turn numbers are on `ChatMessage.metrics`, including `e2e_latency` | Implemented on the new API |
| 7 | Outbound: dial, wait for answer, *then* `session.start()`; catch `api.SipCallError` | The current Vobiz outbound repo starts the session *before* dialling so no callee audio is lost, pins the participant with `room_io.set_participant`, sets `ringing_timeout`, and catches `api.TwirpError` | Phase 3: follow the current repo; verify SIP status mapping on real calls |
| 8 | Log `sip_call_id` | The attribute is `sip.callIDFull` (equals `bridge_uuid` in the Vobiz CDR) | Phase 2 |

## Found after credentials were available

**9. The brief's LLM is deprecated.** `openai/gpt-4.1-mini` on LiveKit Inference retires on
14 January 2027. The default is now `google/gemma-4-31b-it`, LiveKit's recommended model,
which was also the fastest of four compared live. See `PHASE1_VERIFICATION.md`.

**10. Greeting from `on_enter` runs before the room is connected** when the entrypoint
follows the starter's order (`session.start()` then `ctx.connect()`), and fails with
"cannot access local participant before connecting". The greeting is now spoken after
`ctx.connect()`.

## Smaller observations

- **DINAKAR-S code cannot be copied as-is.** It uses `WorkerOptions(entrypoint_fnc=...)`,
  `min_endpointing_delay=` keyword arguments, the Silero plugin and `saaras:v3`. The
  current API is `AgentServer` + `@server.rtc_session`, `TurnHandlingOptions`, and a VAD
  that the session supplies itself. Only its patterns were reused: central `config.py`,
  a short prompt plus a grammar sheet, per-turn latency rows.
- **Its `MAX_TOKENS=140` is risky for Telugu.** Telugu script costs several tokens per
  word, so a two-sentence reply can be cut off. Default here is 250.
- **Its Vobiz notes contradict the brief for Phase 2.** It reports that Vobiz often
  delivers the DID in national `0XXXXXXXXXX` form rather than E.164, and that the SIP
  host must be copied from the LiveKit dashboard, not derived from `LIVEKIT_URL`.
- **The starter has no working unit test.** Its `tests/test_agent.py` is entirely
  commented out; behaviour is covered by `scenarios.yaml` simulations on LiveKit Cloud.
- **Starter features not carried over:** `expressive=True` and STT keyterms are tied to
  its Fish Audio and AssemblyAI defaults, not to Sarvam.
- **`lk` looks for `agent.py` or `src/agent.py`.** Ours is `voice_agent/agent.py`, so
  every `lk agent ...` command takes that path as an argument.
- **`EndCallTool` is in `livekit.agents.beta`**, so its API may still change.
- **`bulbul:v4-flash` exists in the plugin** with Telugu-specific voices
  (`kavitha_te_conversation`, `pooja_te_conversation`, `tarun_te_narration`). The LiveKit
  docs do not mention it yet. Selectable through `SARVAM_TTS_MODEL`; not evaluated.
- **LiveKit Inference can serve the LLM** on the LiveKit project's billing, so Phase 1
  needs no OpenAI key. It is the default provider; direct
  OpenAI and any OpenAI-compatible endpoint are selectable.
- **Brief speaker names check out:** `kavitha`, `kavya`, `aditya`, `shubh` are all valid
  `bulbul:v3` voices. Which sounds most natural in Telugu is unverified.

## Missing from the brief

- No statement of which turn-taking method to use for a language the turn detector does
  not cover (item 1).
- No handling for a reply in English being spoken by a `te-IN` voice. Phase 1 uses one
  Telugu voice for all three styles; per-language voice switching (the DINAKAR-S
  approach) is a possible later improvement.
- No test that enforces the Django/worker separation. Added:
  `tests/test_process_boundary.py`.

## Architectural notes

- The brief lists six Django apps. Phase 1 creates only `apps/core` (health endpoint);
  the others should arrive together with their models in Phase 4 rather than as empty
  placeholders.
- `DATABASE_URL` is parsed with `dj-database-url`. With it unset, Django falls back to a
  local SQLite file. That is acceptable only while there are no models; PostgreSQL is
  required from Phase 4. `psql` is not installed on this machine; Docker is.
- Latency (`700 ms - 1.2 s`) and cost (`under Rs 5.5/min`) figures in the brief come from
  the DINAKAR-S docs, measured on an India VPS at 8 kHz with much tighter endpointing
  than our defaults. They are unverified here and are not targets.

## This machine

- `uv` and `lk` were not installed. Installed: uv 0.13.8 (`py -3.12 -m pip install --user uv`)
  and LiveKit CLI 2.19.3 (`winget install LiveKit.LiveKitCLI`).
- The default `python` is 3.14.4. The project is pinned to 3.12 via `.python-version`.
- No LiveKit project is linked (`lk project list` is empty) and no LiveKit, Sarvam or
  OpenAI keys are present. No live model call could be made.
- `ruff.exe` and `pytest.exe` disappear from `.venv\Scripts` roughly 20 seconds after
  they are run, and `pytest.exe` prints nothing. `python.exe -m pytest` is unaffected.
  The cause was not identified; security software removing unsigned executables is the
  likely one. See the README for the workaround.

## Phase 1 test checklist

Automated (done, see `PHASE1_VERIFICATION.md`): configuration validation, pipeline
construction against the real plugins, prompt budget, greeting, error fallback, latency
logging, process boundary, Django health endpoint.

Needs credentials and a microphone (not done): Telugu / English / Tenglish transcription
quality, voice naturalness, turn-taking with thinking pauses, barge-in, measured latency,
the three `TURN_DETECTION` modes side by side, `scenarios.yaml` simulations.
