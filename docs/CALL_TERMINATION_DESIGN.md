# Call termination design: polite goodbye, then hang up

Status, 14 November 2026: **implemented behind `AGENT_CAN_END_CALL`, which is off by
default.** The tests and scenarios below were written first. After implementation the
8 unit tests pass and the 6 scenarios passed twice on LiveKit Cloud with the flag on;
the 5 existing simulations pass with it off. Not yet tried on a real phone call. The
rest of this document is the design as proposed.

## Why it is needed

Today the agent cannot end a call. On the phone that means the line stays open, and
billed, until the caller hangs up or the trunk's `max_call_duration` (600 s in the
plan) cuts it off mid-sentence.

## Proposed behaviour

1. The caller clearly finishes ("ధన్యవాదాలు, ఉంటాను", "that's all, bye", "phone
   పెట్టేయండి").
2. The model calls an `end_call` tool instead of replying.
3. The agent speaks one short goodbye in the caller's language.
4. When that speech has finished, the session closes and the room is deleted. Deleting
   the room is what disconnects a phone caller: LiveKit ends the SIP call.

The agent must **not** end the call on silence, on "one minute, please", on "thank you"
followed by another question, or on a one-word acknowledgement such as "సరే" or "ఊ".
Hanging up on a caller who was not finished is the worst failure of this feature, so
four of the six scenarios test for it.

## How it would be built

LiveKit ships a prebuilt `EndCallTool` (`livekit.agents.beta.tools`), checked against
the installed 1.8.10 source and the current docs. It does steps 2 to 4 above.

| Piece | Change |
| :--- | :--- |
| `voice_agent/config.py` | New `AGENT_CAN_END_CALL`, default `false`. With it off, the agent is exactly what it is today |
| `voice_agent/call_end.py` (new) | `end_call_tools(settings)` returns `[]` when off, otherwise the tools of one configured `EndCallTool` |
| `voice_agent/assistant.py` | Pass those tools to the agent |
| `voice_agent/prompts/__init__.py` | When on, replace the "reply with one short goodbye" rule with a rule to call `end_call`. The prompt is 1,845 of 2,048 bytes today, so this fits |
| `.env.example` | Document the variable |

`EndCallTool` options:

| Option | Value | Reason |
| :--- | :--- | :--- |
| `delete_room` | `True` | Releases the phone line. In the browser studio it ends the session the same way |
| `ignore_on_enter` | `True` | The tool cannot fire during the greeting |
| `end_instructions` | "Say one short goodbye in the caller's language. No question." | The goodbye is the tool's reply, so it is said once |
| `extra_description` | Do not call on silence, on a request to hold, when the caller has another question, or when unsure | Guards against early hang-ups |

## Open risks to settle in review

| Risk | Detail | Proposal |
| :--- | :--- | :--- |
| The LLM's tool calling is unverified | `google/gemma-4-31b-it` was chosen for speed and Telugu, and never tested with tools | Run the scenarios first; if it calls the tool unreliably, compare models with `scripts/llm_bakeoff.py` before enabling |
| Caller interrupts the goodbye | With the prebuilt tool, the session still closes once the goodbye speech ends, even if the caller said "wait" | Accept for the first version and measure with a scenario; a custom tool that cancels on interruption is the alternative |
| `EndCallTool` is beta | Its API may change between SDK releases | Keep it behind one small module so a change touches one file |
| Speech recognition errors | A misheard "bye" could end a call | The tool description requires a clear ending; the scenarios cover short and unclear replies |
| Double goodbye | The current prompt tells the model to say goodbye itself | The enabled prompt replaces that rule; a unit test checks it |

## Safety nets that do not depend on the model

- `max_call_duration` on the inbound trunk (already in the telephony plan).
- Caller hangs up: the session already closes by itself; no change needed.
- Later, if wanted: a silence timeout that asks "are you there?" and then ends the call.
  Not part of this proposal.

## Tests and scenarios written first

| File | What it covers | State |
| :--- | :--- | :--- |
| `tests/voice/test_call_termination.py` | Off by default; disabled prompt is byte-identical to today; one `end_call` tool when on; description forbids early hang-ups; cannot fire during greeting; room is deleted; one goodbye; prompt budget | Skipped until `voice_agent/call_end.py` exists. Never executed |
| `scenarios/call_termination.yaml` | Six conversations: three that must end the call, three that must not | Not run; kept out of `scenarios.yaml` and CI so the five passing simulations are unaffected |

The unit tests pin the wiring. Whether the model ends calls at the right moments can
only be judged by the scenarios and then by real calls.

## Order of work once approved

1. Run `scenarios/call_termination.yaml` against the unchanged agent to confirm the
   "must end" cases fail for the expected reason.
2. Implement behind `AGENT_CAN_END_CALL=false`; unit tests pass; existing five
   simulations still pass with the flag off.
3. Turn the flag on locally; iterate on the tool description until all six scenarios
   pass; check turns with `lk agent debugger`.
4. Try it in the browser studio, then on a real call once telephony exists.
5. Only then move the scenarios into `scenarios.yaml` and consider enabling by default.
