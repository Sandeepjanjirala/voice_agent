# Phase 1 verification report

Updated 13 November 2026, after LiveKit Cloud and Sarvam credentials were configured.
Machine: Windows 10 Pro, Python 3.12.10, livekit-agents 1.8.10, LiveKit region India South.

## Summary

Everything that can be checked without a person at a microphone has now been run against
the live services, and it works: the worker registers with LiveKit Cloud, Sarvam
recognises and speaks Telugu, English and mixed speech, the LLM replies in the right
language, and all five conversation simulations pass.

**Phase 1 is not yet accepted.** Two of the seven acceptance items need a human voice and
ears: spoken turns through a real microphone, and interruption. Those are sections B to E
below, about 30 minutes.

Three problems were found and fixed during this round (see "Failures found").

## Acceptance gate (brief, section 11)

| # | Item | Status | Evidence |
| :--- | :--- | :--- | :--- |
| 1 | An independent worker starts | **Pass** | `registered worker`, agent `telugu-agent`, region India South |
| 2 | Telugu and Tenglish spoken turns through a real microphone | **Waiting on you** | Full audio pipeline passes with synthetic speech; no human speech yet |
| 3 | Interruption testing | **Waiting on you** | Cannot be automated with the tools available |
| 4 | Selected models and test results logged | **Pass** | This document; models logged at each session start |
| 5 | Django health endpoint runs | **Pass** | `GET /api/health/` returned 200 on a running server |
| 6 | Tests pass | **Pass** | 74 unit tests; 5 of 5 simulations |
| 7 | Unverified performance and cost claims labelled | **Pass** | `docs/AUDIT.md`; latency below is measured, with its limits stated |

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

## Not executed

| Item | Why |
| :--- | :--- |
| Human speech: accents, speed, background noise | Needs you |
| Interruption (barge-in), backchannels, thinking pauses | Needs you |
| Voice naturalness by ear | Needs you |
| `TURN_DETECTION=stt` and `livekit` comparison | Needs you (test D) |
| Network drop mid-conversation | Needs you (test E) |
| `SARVAM_STT_MODE=realtime` | Not tried; plan access unknown |
| Audio-mode simulations (`lk agent simulate audio`) | Not run; text mode and the audio debugger covered the same ground at lower cost |
| PostgreSQL, Docker build, GitHub Actions | Outside Phase 1's local scope |

## Microphone tests for you to run

About 30 minutes. Use headphones so the agent does not hear itself.

First, once: `winget install astral-sh.uv`, then reopen PowerShell (failure 5).

```powershell
cd C:\Users\Sandeep.J\Documents\agent
$env:PYTHONUTF8 = "1"
lk agent console voice_agent/agent.py --record
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
uv run python scripts/latency_report.py
```

**G. Optional.** Other voices: `SARVAM_TTS_SPEAKER=pooja`, `kavya`, `shubh`.

### Results (to be filled in)

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
