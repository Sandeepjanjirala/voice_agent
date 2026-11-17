# Voice responsiveness checkpoint — 2026-11-17

## Approved voice replacement (supersedes the Kavitha settings below)

The owner listened in Sarvam Playground and selected `bulbul:v4-flash`, speaker
`shubh_enhi_ads`, Telugu (`te-IN`), pace `1.0`, and sample rate `24000`.
These values are now in `.env.local` and `.env.example`; the existing API key is
unchanged. The worker already supports this model in its installed plugin.

The employee voice selector now includes "Shubh (Bulbul v4 Flash)". Its preview
uses the matching model and 24 kHz audio. Select it, save and publish to change an
existing employee. Saved classic speakers continue using Bulbul v3 instead of
silently being replaced by the global voice. No employee records were rewritten.

An audio debugger run verified the actual session settings and generated speech:
TTS first audio 205 ms, overall response latency 1730 ms for one synthetic English
turn; the initial greeting was Telugu. These are spot checks, not a latency SLA.
No telephone call was placed. Restart existing worker/backend processes and start
a fresh browser conversation to ensure they load the updated environment.

The remaining sections describe the earlier tuning experiment, not the newly
approved speaker or pace.

The existing Sarvam Kavitha voice remains selected. This change improves delivery
pace and removes repetitive script-like phrasing; it does not establish that the
voice is indistinguishable from a person.

## Configuration

- Default `SARVAM_TTS_PACE=1.2` (previously 1.0).
- Default `MIN_ENDPOINTING_DELAY=0.35` seconds (previously 0.5).
- `PREEMPTIVE_TTS=true` prepares speech during early generation. Playback still
  waits for turn confirmation. Speculation can consume synthesis that is discarded.
- Replies favor one short sentence, a second when needed, and necessary questions
  instead of habitual "anything else?" endings.
- Existing interruption support and the Telugu voice/model remain enabled.

The local ignored configuration was updated too. Restart the voice worker and
start a fresh conversation to load it. Published employee voice settings override
global defaults: choose the faster pace and publish a new version when appropriate.
Existing published versions are not silently rewritten.

## Verification and limits

- 724 pytest tests pass; Ruff formatting/lint and the uv lockfile check pass.
- Synthetic text scenarios cover language switching, concise replies, repeated
  questions, safe limitations and ending calls. They cannot grade voice quality.
- Final run `SR_X3u2bGDwuoGJ` reports six scenario passes, but the CLI exits with
  code 1 and its qualitative summary flags an unsupported promise to relay details
  and inconsistent tool-based hangup on indirect closings. These remain acceptance
  issues; six scenario passes do not establish full conversational correctness.
- Two synthetic Telugu lines produced 11.23 seconds of speech at pace 1.0 and
  8.54 seconds at pace 1.2, about 24% shorter. Neither sample had playback stalls.
  First audio was 0.41–0.46 seconds at 1.0 versus 0.45–0.55 seconds at 1.2;
  faster speech does not mean faster synthesis startup.
- Audio debugger trials observed roughly 1.38–1.49 seconds response latency before
  the last wording adjustment. These are a few synthetic turns, not a controlled
  end-to-end latency benchmark or a production SLA.
- The final audio debugger interrupted an initial response when the synthetic
  caller continued after a sentence pause, then answered the completed request.
  This demonstrates recovery but also why natural pauses must be checked by ear.
  The goodbye turn invoked `end_call` and exited the debugger session.
- Local listening samples: `voice-samples/pace-1.0/kavitha.wav` and
  `voice-samples/pace-1.2/kavitha.wav` (ignored artifacts, not committed).

Before a live pilot, listen to Telugu and mixed-language browser conversations,
including names, class numbers, hesitation, long pauses and interruptions. Confirm
pronunciation and comfort at the new pace. No real telephone call was made for
this tuning. Provider prerequisites and dialing restrictions remain unchanged.

## Rollback

Set `SARVAM_TTS_PACE=1.0`, `MIN_ENDPOINTING_DELAY=0.5` and
`PREEMPTIVE_TTS=false` in the local or deployment configuration, restart the worker
and begin a new session. Restore an employee's published pace separately if changed.
Use the longer endpoint delay if callers are repeatedly cut off during pauses.
