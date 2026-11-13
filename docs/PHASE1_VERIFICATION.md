# Phase 1 verification report

Updated 13 November 2026, after LiveKit Cloud and Sarvam credentials were configured.
Machine: Windows 10 Pro, Python 3.12.10, livekit-agents 1.8.10, LiveKit region India South.

## Summary

**Core acceptance: PASS. Extended speech-quality verification: INCOMPLETE.**
On 13 November 2026 the owner confirmed all ten Django Voice Studio checklist items:
login, connection, audible greeting, Telugu, mixed Telugu-English, interruption,
mute/unmute, end with microphone release, restart, and temporary-network recovery.
These are manual observations, not independently measured stop/recovery timings.
The original 74 tests and five successful simulations are preserved; the expanded
suite now passes 92 tests. No voice worker, prompt, model or endpointing change was made.
Remaining controlled tests are listed below and are not marked passed.

## Acceptance gate (brief, section 11)

| # | Item | Status | Evidence |
| :--- | :--- | :--- | :--- |
| 1 | An independent worker starts | **Pass** | `registered worker`, agent `telugu-agent`, region India South |
| 2 | Telugu and Tenglish spoken turns through a real microphone | **Pass (owner)** | Telugu and Telugu-English mixed speech confirmed in Django Voice Studio |
| 3 | Interruption testing | **Pass (owner, functional)** | Owner can interrupt while AI speaks; exact stop latency, false interruptions and response correctness under repeated interruption remain unmeasured |
| 4 | Selected models and test results logged | **Pass** | This document; models logged at each session start |
| 5 | Django health endpoint runs | **Pass** | `GET /api/health/` returned 200 on a running server |
| 6 | Tests pass | **Pass** | 92 tests now pass, including the original 74; earlier 5 of 5 simulations preserved, not rerun |
| 7 | Unverified performance and cost claims labelled | **Pass** | `docs/AUDIT.md`; latency below is measured, with its limits stated |

## Earlier Cloud real-microphone update - 13 November 2026

Evidence: owner-reported LiveKit Cloud Console session and dashboard readings. Four
screenshots were mentioned, but image attachments were not available in this coding
session; no independent screenshot inspection is claimed.

| Cloud session metric | Value |
| :--- | :--- |
| Turns / STT audio | 10 / 22 seconds |
| Interrupted / backchannel rate | 40% / 10% |
| Average reply latency | approximately 1,575 ms |
| STT delay / EOT | 538 ms / 47 ms |
| LLM TTFT (Gemma 4 31B) / TTS TTFB (bulbul:v3) | 478 ms / 217 ms |
| bulbul:v3 P50 / P95 | 198 ms / 215 ms |
| Gemma 4 31B P50 / P95 | 361 ms / 686 ms |

Local `scripts/latency_report.py` was independently rerun during this update and
matches the owner's report: 28 sessions, 165 logged turns, 4/97 agent turns interrupted.

| Local stage | Samples | P50 | P90 |
| :--- | ---: | ---: | ---: |
| End-of-turn detection | 21 | 0.723 s | 0.835 s |
| STT transcription delay | 21 | 0.723 s | 0.833 s |
| LLM TTFT | 68 | 0.370 s | 0.772 s |
| TTS TTFB | 25 | 0.194 s | 0.213 s |
| Perceived e2e latency | 21 | 1.496 s | 2.251 s |

Safe interpretation: the local report aggregates different sessions and includes only
rows containing each metric; 165 rows are not 165 audio latency samples. Its interruption
denominator differs from the single Cloud session. P90 is not P95, and a median is not
an average. Do not sum stage percentiles or count overlapping STT/turn-detection delays
twice. Cloud's listed components sum to 1,280 ms, not 1,575 ms; the available summary
cannot attribute the difference. Do not infer an exact critical-path breakdown.
The earlier claim that a particular share is solely endpointing is a hypothesis,
not a conclusion established by these aggregates. Keep all model, voice, prompt and
endpointing settings unchanged until controlled paired tests establish an improvement.

| Remaining criterion | Status / evidence needed |
| :--- | :--- |
| Telugu microphone connection and response | Owner-confirmed pass |
| Naturalness, accents, fast speech | Pending structured listening/transcript review; mixed Telugu-English now owner-confirmed |
| Barge-in stops promptly and answers the new turn | Functional interruption owner-confirmed; stop latency and repeated-interruption response correctness not measured |
| Backchannels and noise do not falsely interrupt | Pending; rate alone is insufficient |
| Mid-sentence pauses and short replies | Pending controlled tests C |
| VAD/STT/LiveKit mode comparisons | Pending controlled tests D |
| Network drop and recovery | Owner-confirmed pass after temporary interruption; outage length and recovery timing unspecified |

## Configuration check

Checked without displaying any secret (only whether each value is set, and its length).

| Setting | Result |
| :--- | :--- |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Set, well-formed; worker registered with them |
| `SARVAM_API_KEY` | Set; STT and TTS calls succeeded |
| `OPENAI_API_KEY` | Not set; not needed with `LLM_PROVIDER=livekit` |
| `DJANGO_SECRET_KEY` | Set |
| All other settings | Valid; resolved to the models below |

Models in use: STT `saaras:v4`, task `codemix`, language `te-IN`, 16 kHz. TTS `bulbul:v3`,
voice `kavitha`, `te-IN`, 22.05 kHz, pace 1.0. LLM `google/gemma-4-31b-it` through LiveKit
Inference. Turn detection `vad`, endpointing 0.5 s to 3.0 s, interruption mode `auto`.

## LLM: the original default was deprecated

`openai/gpt-4.1-mini` still answers, but LiveKit lists it as **deprecated, retiring
14 January 2027**, and names `google/gemma-4-31b-it` as the replacement. Four current
models were compared on the agent's own prompt (5 turns x 2 rounds each, live):

| Model | Status | Median time to first token | Telugu replies |
| :--- | :--- | :--- | :--- |
| `google/gemma-4-31b-it` | Current, LiveKit's recommended default | **0.40 s** | Natural spoken forms ("సరేనండి", "అండి") |
| `openai/gpt-4.1-mini` | Deprecated | 0.59 s | Good; misused "గారు" without a name |
| `openai/gpt-5.4-mini` | Current | 0.70 s | Good |
| `google/gemini-3.5-flash-lite` | Current | 1.16 s | Wordy; repeated its introduction every turn |

The default is now `google/gemma-4-31b-it`. All four answered English in English and
understood romanised Telugu ("naaku repu appointment kavali"). Telugu quality here is my
reading of the text, not a native-speaker review. Tool calling, which Phase 4 depends on,
was not compared. Re-run with `uv run python scripts/llm_bakeoff.py <model> ...`.

## What was executed

### Unit tests: 74 passed

`uv run python -m pytest`, no network, about 2.5 s. Lint and format: clean.

### Live LLM, text mode (`lk agent debugger`)

Four turns through the real worker. Telugu question answered in Telugu, English in
English, a mixed booking request in mixed Telugu-English, AI disclosure given, no fee
invented. Time to first token 0.36 s to 0.87 s.

### Live speech round trip (Sarvam TTS, then Sarvam STT)

Each line was spoken by the configured voice and transcribed back by the configured STT.

| Spoken | Transcribed | TTS first audio | Transcript after speech ended |
| :--- | :--- | :--- | :--- |
| నమస్తే! నేను మీ AI సహాయకురాలిని. మీకు ఏ విధంగా సహాయం చేయగలను? | Identical except "!" became "," | 0.33 s | 0.78 s |
| నాకు రేపు ఉదయం పది గంటలకు రావాలని ఉంది. | Identical | 0.19 s | 0.39 s |
| Hello, can you speak in English? | Identical except a comma | 0.19 s | 0.72 s |
| నాకు రేపు morning ఒక appointment కావాలి. | Identical, English words kept in Latin letters | 0.20 s | 0.34 s |
| నా phone number తొమ్మిది ఎనిమిది నాలుగు ఎనిమిది సున్నా. | Identical | 0.19 s | 0.14 s |

This is clean synthetic speech from Sarvam's own voice. It proves the keys, models,
language and code-mix settings work. It says nothing about real accents, speed or noise.

### Full audio pipeline (`lk agent debugger --audio`)

The CLI speaks each user line into the agent's audio input, so STT, turn detection, LLM
and TTS all run. 13 turns across two sessions; every turn was understood and answered in
the right language.

What the agent heard, where it differed from what was sent:

| Sent | Heard | Effect |
| :--- | :--- | :--- |
| పది గంటలకు. | 10 గంటలకు. | None; reply said "పది గంటలకు" |
| నా పేరు రమేష్. | నా పేరు Ramesh. | Name arrives in Latin letters; reply used "Ramesh గారు" |
| మీ office address ఏంటి... | Mi office address ఏంటి? | One word misheard; meaning kept |

The ten-digit phone number was heard exactly and read back digit by digit. The user voice
in this mode is a LiveKit synthetic voice, not a Telugu speaker.

### Measured latency

From `scripts/latency_report.py`, first audio session, 11 user turns:

| Stage | n | Median | 90th percentile | Max |
| :--- | :--- | :--- | :--- | :--- |
| End-of-turn detection | 11 | 0.72 s | 0.82 s | 0.84 s |
| STT transcription delay | 11 | 0.72 s | 0.81 s | 0.83 s |
| LLM time to first token | 11 | 0.39 s | 1.12 s | 1.19 s |
| TTS time to first byte | 12 | 0.19 s | 0.20 s | 0.22 s |
| **Perceived turn latency (end of speech to start of reply)** | 11 | **1.49 s** | **2.25 s** | **2.39 s** |

Reading it:

- The largest share is waiting to be sure the caller has finished: 0.72 s, of which 0.5 s
  is the configured `TURN_ENDPOINTING_MIN_DELAY`. Lowering it is the main lever, at the
  risk of cutting callers off. Test D below is how to choose.
- The LLM was above 1 s on 2 of 11 turns; the rest were 0.34 s to 0.63 s.
- This is above the `0.7 s - 1.2 s` range quoted in the brief. That range was measured
  elsewhere with 0.15 s endpointing at 8 kHz, so the two are not like for like.
- Measured from this laptop on office internet with synthetic speech; a small sample.
  Treat it as a first reading, not a benchmark.

### Simulations (`scenarios.yaml`, LiveKit Cloud, text mode)

**5 of 5 passed** on the final code, run one at a time.

| Scenario | Result |
| :--- | :--- |
| Greets in Telugu and discloses it is an AI | Pass |
| Answers English in English | Pass |
| Handles Telugu-English mixed speech | Pass |
| Does not invent facts | Pass |
| Declines a harmful request | Pass |

Five runs were made in total. Two sequential runs passed 5 of 5. Three parallel runs
lost 1, 4 and 2 scenarios, all for infrastructure reasons and none on a judged
conversation; see failure 3.

### Worker and Django

`voice_agent/agent.py start` registered with LiveKit Cloud (India South) under
`telugu-agent`. Django `check` is clean and `/api/health/` returned 200 (previous round).

## Failures found

| # | Failure | Cause | Status |
| :--- | :--- | :--- | :--- |
| 1 | Default LLM deprecated, retiring 14 January 2027 | Model chosen from older references | **Fixed**: default is now `google/gemma-4-31b-it` |
| 2 | `cannot access local participant before connecting` in the worker log | The greeting was spoken from `on_enter`, which runs before the room connects | **Fixed**: greeting is spoken after `ctx.connect()`; error no longer appears |
| 3 | Simulations fail with "No agent joined room within 30s" when run in parallel | The LiveKit native library times out connecting several rooms at once on this machine (`FFI Panic: timed out waiting for ReadyForRoomEventRequest`). Seen before and after fix 2 | **Not fixed**, outside our code. Workaround: `--concurrency 1`. Whether it also happens on Linux is unknown and matters for concurrent calls later |
| 4 | Agent used "గారు" with no name, said it would "check", and asked for a name or number it cannot store; reopened the conversation after "thank you" | Prompt wording | **Fixed** in the prompt and style sheet; the judge reported no recurring defect on the final run |
| 5 | `lk agent ...` reports "livekit-agents is not installed" | `uv` is not on PATH (it was installed with pip under Python 3.12) | **Action for you**: `winget install astral-sh.uv`, then reopen PowerShell |
| 6 | Event loop blocked 120 to 320 ms at session start | Windows certificate store loading inside the SDK, once per process | Not ours; noted |

## Outstanding or partially executed checks (updated after owner feedback)

| Item | Why |
| :--- | :--- |
| Controlled human speech: accents, speed, background noise | Basic Telugu microphone conversation confirmed; these variants remain pending |
| Backchannels, thinking pauses, noise and timed barge-in | Functional barge-in passed by owner; these controlled variants remain pending |
| Voice naturalness by ear | Needs you |
| `TURN_DETECTION=stt` and `livekit` comparison | Needs you (test D) |
| Network drop mid-conversation | Owner reports recovery works; exact ten-second test, repeatability and elapsed recovery not documented |
| `SARVAM_STT_MODE=realtime` | Not tried; plan access unknown |
| Audio-mode simulations (`lk agent simulate audio`) | Not run; text mode and the audio debugger covered the same ground at lower cost |
| PostgreSQL, Docker build, GitHub Actions | Outside Phase 1's local scope |

## Microphone tests for you to run

About 30 minutes. Use headphones so the agent does not hear itself.

For direct Windows execution, use the existing virtual environment; global uv is not required.

```powershell
cd C:\Users\Sandeep.J\Documents\agent
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m voice_agent.agent console --record
```

Keys: `m` mutes, `Ctrl+T` switches to typing, `q` quits.

**A. Startup.** You should hear a Telugu greeting within a few seconds. Note whether the
voice sounds natural.

**B. Language.** Say each line; note what the transcript shows and how the reply sounds.

| # | Say | Look for |
| :--- | :--- | :--- |
| 1 | నమస్తే, మీరు ఎవరు? | Telugu transcript in Telugu script; Telugu reply; says it is an AI |
| 2 | నాకు రేపు ఉదయం పది గంటలకు రావాలని ఉంది | Time understood correctly |
| 3 | Hello, can you speak in English? | English transcript; reply in English (expect a Telugu accent) |
| 4 | నాకు రేపు morning ఒక appointment కావాలి | "morning" and "appointment" kept as English words; mixed reply |
| 5 | Your own phone-number-style digits in Telugu | Digits captured and read back correctly |
| 6 | మీ office address ఏంటి, fee ఎంత? | Does not invent an address or a fee |
| 7 | A sentence in your own accent at natural speed | Transcript accuracy |

**C. Turn-taking and interruption.**

| # | Do | Look for |
| :--- | :--- | :--- |
| 8 | Start a sentence, pause about one second mid-sentence, then finish | Agent waits for you to finish |
| 9 | Answer with only "అవును" or "సరే" | Short reply is picked up |
| 10 | Interrupt the agent mid-sentence with a new question | It stops quickly and answers the new question |
| 11 | Say "హా" or "ఊ" while it speaks | It keeps talking |
| 12 | Cough or tap the desk while it speaks | It keeps talking, or resumes after a brief stop |

**D. Compare turn-taking modes.** Quit with `q`, change `TURN_DETECTION` in `.env.local`,
restart, and repeat tests 8 to 10 for `vad`, `stt` and `livekit`. Also try
`TURN_ENDPOINTING_MIN_DELAY=0.3` (faster replies) and `0.8` (more patient).

**E. Failure.** Disconnect from the network for ten seconds mid-conversation, reconnect,
and speak again. Note whether you hear the Telugu apology and whether the conversation
continues.

**F. Latency.** After at least 15 turns, in a second window:

```powershell
.\.venv\Scripts\python.exe scripts/latency_report.py
```

**G. Optional.** Other voices: `SARVAM_TTS_SPEAKER=pooja`, `kavya`, `shubh`.

### Detailed controlled-test worksheet (unfilled items remain incomplete)

| Item | Result |
| :--- | :--- |
| Date, tester, microphone, room noise | |
| Tests 1-7: transcription accuracy and reply language | |
| Voice naturalness; words mispronounced | |
| Test 8 (pause) | |
| Test 9 (short reply) | |
| Test 10 (interrupt) | |
| Tests 11-12 (backchannel, noise) | |
| Best `TURN_DETECTION` mode and endpointing delay | |
| Failure test E | |
| Latency table | |

## Known limitations

- **One Telugu voice speaks everything**, so English replies carry a Telugu accent.
- **Turn-taking is pause-based** because the LiveKit turn detector does not list Telugu.
  It costs about 0.7 s per turn at the current setting.
- **Names come back in Latin letters** in `codemix` mode ("Ramesh"), and numbers as
  digits ("10"). Fine for the LLM; relevant when names are stored in Phase 4.
- **Defaults are untuned by ear**: voice, pace and endpointing delay.
- **Microphone settings, not telephone settings.** 16 kHz in, 22.05 kHz out. Phone lines
  are 8 kHz; quality and latency will differ in Phase 2.
- **The agent cannot do anything yet.** No tools, booking, transfer or hang-up.
- **The LLM's tool-calling ability is unverified**, and LiveKit retires models on a
  schedule, so the model choice needs revisiting in Phase 4.
- **Transcripts are logged by default** (`VOICE_LOG_TRANSCRIPTS=true`). Turn this off
  before real callers are involved.
- **Parallel sessions failed to connect on this Windows machine** (failure 3).
- **Tool launchers vanish on this machine**: `pytest.exe` and `ruff.exe` are deleted from
  `.venv\Scripts` shortly after running. Workaround in the README.

## Final review evidence - 13 November 2026

Manual source: the owner's ten-item response in this conversation, all checked working.
No failures, device/browser details or exact outage duration were reported. This closes
basic Telugu/Tenglish, functional interruption and browser-network-recovery checks;
it does not supply a voice-naturalness rating or results for pauses/backchannels/modes.

Available local evidence before this review's synthetic smoke test:
`logs/turn_metrics.jsonl`, 184 rows, 32 sessions, 9/108 assistant turns marked interrupted.
Snapshot SHA256: `083db234186d1f0745db8edd9bc05122b3f1f1b57033b52dc3c0a6a03372c04b`.
This is a historical snapshot; subsequent smoke tests append metrics normally.

| Aggregate stage | Samples | P50 seconds | P90 seconds |
| :--- | ---: | ---: | ---: |
| End-of-turn | 29 | 0.731 | 0.901 |
| STT transcription | 29 | 0.730 | 0.899 |
| LLM TTFT | 75 | 0.372 | 0.772 |
| TTS TTFB | 36 | 0.193 | 0.209 |
| Perceived e2e | 28 | 1.511 | 2.024 |

Two later browser sessions contain user turns (times in Asia/Kolkata):

| First logged turn | Rows / user / assistant | Interrupted assistant turns | e2e samples / P50 / P90 |
| :--- | :--- | :--- | :--- |
| 15:59:51 | 10 / 5 / 5 | 1/5 | 4 / 1.611 s / 1.908 s |
| 16:01:19 | 7 / 3 / 4 | 2/4 | 3 / 1.527 s / 1.669 s |

These likely correspond to the owner's tests, but no explicit session mapping was
provided. The metrics contain character counts, not transcript text, raw audio,
connection events or mute events. They cannot diagnose a mistranscribed Telugu word,
rate naturalness, establish intended versus false barge-in, or prove network recovery.
Interrupted flags also occur when a test ends during a greeting. No Telugu engine bug
is confirmed by these records, so the working engine and configuration remain unchanged.

Browser edge-case fixes and security results are in PHASE1_5_VERIFICATION.md.
**Stop before Phase 2 for owner review.** Naturalness, accents/noise, pauses, short-reply
accuracy, backchannels, comparative turn modes and timed/repeated failure testing remain
incomplete. No claim of a full production or audio-quality certification is made.
