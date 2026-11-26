# Phase 2 readiness report and plan: real incoming calls

Prepared 13 November 2026. Nothing was purchased, configured, called, deployed or
committed to produce this report. No Phase 2 code exists yet.

**Status: ready to plan, not ready to buy.** One compatibility question and a handful of
price questions must be answered first (section 9). They cost nothing to answer.

How to read the evidence labels:

- **Verified**: read today on the vendor's own page, in the LiveKit docs, or in the
  installed SDK.
- **Reported**: from a secondary source (law-firm note, vendor blog, search snippet).
- **Not published**: looked for and not found. Ask the vendor.

Provider and regulation pages were read through a summarising fetcher. Re-check any
figure on the linked page before paying. Section 8 is planning input, not legal advice.

## 1. Recommendation

1. **Plivo (India-region account) is the first choice**, provided the question in 9.1 is
   answered yes. It is the only provider with published rupee prices (number ₹200/month,
   inbound ₹0.38/min), self-serve trunks, TLS and SRTP at no charge, and a setup guide on
   both its own site and LiveKit's.
2. **Vobiz is the fallback**. It is the quickest to start (individual or company KYC in
   minutes, self-serve, an official LiveKit guide and example repositories we already
   audited), but its India prices, media encryption and licence holder are not published.
3. **Exotel** works with LiveKit and documents TLS and SRTP, but trunks are set up by its
   support team and SIP pricing is by quote. Consider it if you want an account manager.
4. **Twilio, Telnyx and LiveKit Phone Numbers are out** for now: Twilio has no local +91
   numbers, Telnyx needs an address in one of four districts (none in Telangana or AP),
   and LiveKit sells US numbers only.

Expected cost for a month of testing with about 300 inbound minutes on Plivo: roughly
₹314 plus GST, plus Sarvam usage. LiveKit's free plan includes 1,000 third-party SIP
minutes. Details in section 7.

## 2. Protecting the working project

### What was checked

| Check | Result |
| :--- | :--- |
| Working tree | Clean. Branch `master`, two commits, head `f33d3fc` "completed phase 1.5" |
| Remote | None. The only copy of the history is this laptop |
| `.env.local` | Ignored, never committed in either commit |
| `db.sqlite3`, `logs/`, `console-recordings/`, `browser-artifacts/`, `.test-tmp/`, `node_modules/` | Ignored, never committed |
| Secrets in tracked files and in both commits | None found. The only matches are an explicit insecure development placeholder and dummy test values |
| `.dockerignore` | Excludes `.env` and `.env.*`, so a container build cannot pick up local secrets |

The secret scan was a pattern search for key-like assignments, not a dedicated scanner.

### Gaps to close before Phase 2

| Gap | Why it matters | Proposed fix |
| :--- | :--- | :--- |
| No off-machine copy | A disk failure loses the project | Push to a private remote, or copy a `git bundle` to company storage |
| `.env.local` has no backup | Git does not hold it by design | Store the keys in a password manager |
| No tag on the accepted state | Hard to return to exactly what passed | Annotated tag `phase-1.5-accepted` on `f33d3fc` |
| CI targets `main`, the branch is `master` | Neither workflow would ever run after a push | Rename the branch to `main`, or change both workflows |
| Telephony files are not ignored yet | LiveKit's CLI flow uses JSON files that can hold a trunk password; call captures and audio hold caller speech | Add the lines below |
| Commits use a personal Gmail identity | Your call; flagging in case company identity is expected | `git config user.email` if needed |

Proposed `.gitignore` additions:

```gitignore
# Telephony (Phase 2): trunk files may hold SIP passwords; captures hold caller audio
*-trunk.json
dispatch-rule.json
sip-participant.json
*.pcap
*.pcapng
*.wav
*.mp3
recordings/
transcripts/
*.pem
*.key
*.p12
*.sqlite3
```

### Recommended checkpoint (needs your approval; nothing was run)

```powershell
git tag -a phase-1.5-accepted f33d3fc -m "Phase 1 core + Phase 1.5 local functional acceptance"
git bundle create ..\agent-phase-1.5.bundle --all   # copy this file off the laptop
git switch -c phase-2-inbound                        # all Phase 2 work happens here
```

Phase 2 work on its own branch leaves the accepted Django studio and voice engine
untouched on `master` until you review and merge.

## 3. Outstanding voice-quality risks

No STT, LLM, TTS or endpointing setting is changed by this plan. The items below are
still open from Phase 1 and 1.5, and each needs your voice or ears.

| Open item | Needs your voice | Why it matters more on a phone |
| :--- | :--- | :--- |
| Naturalness of the `kavitha` voice, mispronounced words | Yes | Phone audio is 8 kHz; flaws are more audible |
| Accents, fast speech, background noise | Yes | Callers are on mobiles in streets and vehicles |
| Mid-sentence pause (about 1 s) | Yes | Phone lines add delay, so callers pause more |
| One-word replies ("అవును", "సరే") | Yes | Short words are the first casualty of narrowband audio |
| Backchannels ("హా", "ఊ") and coughs while the agent speaks | Yes | See the interruption note below |
| `TURN_DETECTION` `vad` vs `stt` vs `livekit`, and endpointing 0.3 / 0.5 / 0.8 s | Yes | The right value over a phone line may differ from the microphone value |
| Timed and repeated network outage | Yes | |
| `SARVAM_STT_MODE=realtime` | No, needs plan access | |
| Parallel sessions on this Windows machine | No | Two callers at once may hit the earlier connection timeouts |

Three things the existing measurements already suggest:

- **Interruption rate.** In your first microphone session 4 of 9 agent replies were cut
  off. That is expected if you interrupted on purpose and a problem if you did not.
  Speakerphone echo on a real call would make it worse. Worth a deliberate test.
- **Latency.** Your microphone session measured 1.58 s average from end of speech to
  start of reply (median 1.66 s). A phone line adds its own delay on top. Not measured.
- **Noise-cancellation allowance.** The worker uses the ai-coustics `QUAIL_VF_S` model.
  LiveKit's price list counts it as "voice isolation" with 100 minutes included on the
  free plan. What happens after 100 minutes on that plan is not stated.

New phone-only questions, to be measured in Phase 2 rather than guessed:

| Question | How it will be measured |
| :--- | :--- |
| Does 8 kHz phone audio hurt Telugu recognition? | Same seven test sentences by phone; compare transcripts with the microphone run |
| Should `SARVAM_STT_SAMPLE_RATE` and `SARVAM_TTS_SAMPLE_RATE` move to 8000? | A/B on real calls; change only if transcripts or latency improve |
| ai-coustics in the worker vs `krisp_enabled` on the trunk | A/B on real calls; never both at once |

## 4. Provider comparison

| | Plivo (India region) | Vobiz | Exotel |
| :--- | :--- | :--- | :--- |
| +91 number types | Landline series such as 080, 022. Hyderabad 040 not confirmed | 1800, 140, 160, "92" series; ordinary local numbers not listed as a category, though its guide uses an 080 number | "Exophones", regional incl. AP |
| Buying | Self-serve console | Self-serve console or API | Email to support |
| Who can sign up | India-registered business | Individual (Aadhaar + PAN) or company (PAN + GST) | Business or individual |
| KYC documents | One of GST certificate, incorporation certificate, Udyam. A second Plivo page says both GST and incorporation | As above | PAN, GSTIN, signatory verification |
| Approval time | About 5 minutes (another page: up to 1 business day) | "Under 2 minutes" | 1 to 3 business days (reported) |
| Trial | India trial via sales form (reported) | Trial numbers are outbound only; inbound needs KYC and a paid number | 7 days, ₹500 usage; no number without KYC |
| Number rental | ₹200/month (a snippet showed ₹250) | Not published | Not published for SIP; bundles start at ₹9,999 for 5 months |
| Setup fee | Not published | Charged with first month; amount not published | Not published |
| Inbound rate | ₹0.38/min, 30 s pulse | "From ₹0.45/min"; inbound rate not published | Not published |
| Channels | Not published | Paid monthly subscription; whether required for inbound is unclear | 20 calls/min per trunk default |
| Send calls to LiveKit's SIP address | Yes, self-serve | Yes, self-serve | Yes, done by support |
| Signalling encryption (TLS) | Yes | Listed as a transport option | Yes |
| Media encryption (SRTP) | Yes, no charge | Not published | Yes |
| Trunk authentication | Not confirmed for inbound | Digest credentials and/or IP list | Digest credentials (reported) and IP list |
| Codecs | Not stated | G.711 µ-law, G.722 | G.711 A-law, µ-law |
| LiveKit guide | Both sides | Vobiz side and two repositories; none on LiveKit's side | Exotel side only; a community thread reports 503 errors |
| Licence holder stated | Not checked | Not published | Not checked |
| Company | Established, global | Ilaimitado Private Limited, founded 2025 (reported); "all payments final" | Established, Indian |

All cells are **Verified** on the provider's own pages unless marked otherwise.

Also checked:

- **LiveKit Phone Numbers**: US only. Verified.
- **Twilio**: no local or mobile India numbers; toll-free only, with an address outside
  India. Reported.
- **Telnyx**: India mobile numbers since September 2026, address must be in Mumbai,
  Gurugram, Noida or Bangalore, heavy document list, prices not published. Verified.
- **Tata, Airtel IQ, Jio, Ozonetel, Knowlarity, MyOperator, Acefone, Kaleyra**: no public
  self-serve SIP pricing found; assume sales-led.

## 5. How LiveKit handles an incoming call

All of this section is **Verified** against the LiveKit docs and `livekit-api` 1.2.1.

### Pieces

- **SIP endpoint.** The address the provider sends calls to:
  `<project-subdomain>.sip.livekit.cloud`. The India-only form is
  `<project-subdomain>.india.sip.livekit.cloud`.
- **Inbound trunk.** Created once in LiveKit. Says which dialled number to accept and
  how to authenticate the provider.
- **Dispatch rule.** Created once. Says which room each call goes to and which agent
  joins. Setting `room_config.agents[0].agent_name` to `telugu-agent` reuses the
  existing worker, exactly as the Django studio token already does.

Trunks and rules are long-lived. LiveKit warns against creating them per call.

### Lifecycle

1. Caller dials the +91 number. The provider sends a SIP INVITE to the LiveKit endpoint.
2. LiveKit matches the inbound trunk (number, credentials or source address). A
   mismatch is rejected with 403.
3. LiveKit matches a dispatch rule, creates the room and adds the caller as a SIP
   participant with status `ringing`.
4. The rule dispatches `telugu-agent`. The caller hears ringing until the agent
   publishes audio; then the status becomes `active` and the greeting plays.
5. Caller hangs up: the participant leaves with reason `CLIENT_INITIATED` and the
   agent session closes by itself. No code needed.

### Failure handling

| Failure | What happens | Guard |
| :--- | :--- | :--- |
| Worker not running | Caller hears ringing until `ringing_timeout`, then busy (486) | Set `ringing_timeout` to about 25 s; keep the worker running during tests |
| STT or LLM fails mid-call | Existing spoken Telugu apology | Already built in Phase 1 |
| Worker crashes mid-call | Caller left in silence | `max_call_duration` on the trunk; room empties after the caller hangs up |
| Call never ends (line left open) | Minutes keep billing | `max_call_duration`, proposed 600 s for testing |
| Agent needs to hang up | Not possible today | Optional step 2b below |

### Security

- **Signalling** is encrypted when the provider's destination address ends in
  `;transport=tls`.
- **Media** is encrypted when the trunk has `media_encryption` set to
  `SIP_MEDIA_ENCRYPT_REQUIRE` or `_ALLOW` and the provider has SRTP on. It is off by
  default. LiveKit supports the AES-128 and AES-256 SRTP suites.
- **Who may send calls.** A trunk that only lists a number accepts a call to that number
  from any address. `allowed_addresses` is saved but **not enforced** until LiveKit
  support enables it for the project. So the trunk needs username and password
  authentication if the provider supports it, or that enablement.
- **Caller numbers in room names.** LiveKit's usual "individual" rule names each room
  after the caller's number, which then appears in logs and in our latency file. The
  plan uses a "callee" rule with `randomize` instead, so the room is named after our
  own number plus a random suffix.

## 6. Implementation plan

The voice engine is not rewritten. Models, prompt, greeting, endpointing and the Django
studio stay as they are.

### Step 2a: required

| Area | Change |
| :--- | :--- |
| `apps/telephony/` (new Django app) | `services.py` builds the inbound-trunk and dispatch-rule requests from settings and calls `livekit.api`. No models: LiveKit stays the source of truth until Phase 4 |
| Management command | `manage.py telephony plan` (default, prints what would be created, no network writes), `status`, `apply`, `teardown`. `apply` is idempotent by name and never prints the trunk password |
| Staff-only page in the studio | Read-only "Telephony" page: trunk, number, dispatch rule, encryption setting, last check. No edit buttons in Phase 2 |
| `voice_agent/telephony.py` (new) | Pure helpers: detect a SIP caller, read `sip.*` attributes, mask the caller number for logs (`+91XXXXXX1234`) |
| `voice_agent/agent.py` | After connecting, wait for the participant; if it is a SIP caller, log the masked number, trunk and call ID. About ten added lines; the browser path is unchanged |
| `voice_agent/observability.py` | Add `channel` (`sip` or `web`) to each latency row so phone and microphone numbers can be compared |
| `scripts/latency_report.py` | Optional `--channel` filter |
| `.env.example`, README, this folder | New variables, run steps, `docs/PHASE2_VERIFICATION.md` |

Django may import `livekit.api`; it still never imports `livekit.agents` or
`voice_agent`. The boundary test keeps enforcing that.

New environment variables, all documented in `.env.example` and all optional until
`telephony apply` is run:

| Variable | Purpose |
| :--- | :--- |
| `TELEPHONY_PROVIDER` | `plivo`, `vobiz` or `exotel`; used for labels and provider-specific checks |
| `TELEPHONY_INBOUND_NUMBER` | The purchased number in `+91…` form |
| `SIP_INBOUND_AUTH_USERNAME`, `SIP_INBOUND_AUTH_PASSWORD` | Trunk credentials, if the provider supports them. Secret |
| `SIP_INBOUND_ALLOWED_ADDRESSES` | Provider signalling addresses, comma-separated |
| `SIP_MEDIA_ENCRYPTION` | `require`, `allow` or `off`. Default `allow` |
| `SIP_RINGING_TIMEOUT_SECONDS` | Default 25 |
| `SIP_MAX_CALL_DURATION_SECONDS` | Default 600 |
| `SIP_HIDE_CALLER_NUMBER` | Default `true`: the agent does not receive the number at all |
| `SIP_NOISE_CANCELLATION` | `agent` (current behaviour) or `trunk`; for the A/B test only |

### Step 2b: optional, only with a scenario written first

Letting the agent hang up (LiveKit's `EndCallTool`, still in beta) and a silence timeout
change agent behaviour. Per the project rules, a `scenarios.yaml` case comes first and
the five existing simulations must still pass. Proposed as a separate reviewable change.

### Tests

| Test | Needs network or a call |
| :--- | :--- |
| Trunk and dispatch requests built correctly from settings: number format, agent name, callee rule with random suffix, encryption, timeouts | No |
| `plan` writes nothing; `apply` is idempotent; password never appears in output or logs | No, API mocked |
| Telephony page: staff only, read-only, no secret in the response | No |
| SIP caller detection and number masking, with fake participants | No |
| Latency rows carry `channel` | No |
| Existing 92 tests, boundary test, `.env.example` completeness, Ruff | No |
| Existing five simulations | LiveKit Cloud, no call |
| `telephony status` against the real project before any trunk exists | LiveKit Cloud, read-only |
| Real inbound calls | **Yes: you, from your own phone, after approval** |

### Acceptance criteria for Phase 2

1. A call from your mobile to the +91 number is answered with the Telugu greeting.
2. Ten consecutive calls connect, each in its own room, with no caller number in any
   room name or log line.
3. Telugu, English and mixed turns are understood by phone; transcripts recorded for
   the seven Phase 1 test sentences.
4. Interrupting the agent by phone stops it; hanging up closes the session within a few
   seconds and leaves no room open.
5. With the worker stopped, the caller gets a busy signal within the ringing timeout.
6. Signalling uses TLS. Media encryption is on, or its absence is recorded as a
   provider limitation you accepted.
7. A call from an address or credential that is not the provider's is rejected.
8. Phone latency is measured over at least 30 turns and reported next to the microphone
   figures. No target is claimed in advance.
9. All automated tests and the five simulations pass.
10. Actual spend for the test period is recorded against the estimate below.

Not in Phase 2: outbound calls, call records or transcripts in a database, appointment
booking, deployment. Those are Phases 3 to 5.

## 7. Expected cost

| Item | Plivo, verified prices | Vobiz |
| :--- | :--- | :--- |
| Number rental | ₹200/month | Not published |
| Setup | Not published | Not published |
| 300 inbound minutes | ₹114 | About ₹135 if ₹0.45/min applies to inbound |
| GST | 18% assumed on top | Not published |
| **Provider total, first month** | **about ₹370** | **Unknown until the console shows prices** |

LiveKit, free "Build" plan (Verified): 1,000 third-party SIP minutes included; then
$0.004/min on the $50 plan. $2.50 of inference credit is included; LiveKit's own
calculator puts the current LLM at about $0.0014/min. Voice isolation: 100 minutes
included (see section 3).

Sarvam STT and TTS are billed by Sarvam at your plan's rates; not checked for this
report. LiveKit's planning figure for any third-party SIP vendor is about $0.008/min,
which is in line with the Plivo rate.

Cost controls in the plan: one number, `max_call_duration` of 10 minutes, calls only
from your own phones during testing, and prepaid balance kept small.

## 8. Regulation and caller privacy

Sources here are mostly law-firm summaries and one TRAI press release read directly.
DoT's own site could not be loaded. Treat this as a checklist to confirm, not advice.

| Topic | Position | Evidence |
| :--- | :--- | :--- |
| Who may connect phone calls to SIP | Only a DoT-authorised provider. We receive calls over its trunk and never bridge phone and internet legs ourselves | Reported |
| OSP registration | Removed in 2021; not needed to answer our own calls. Whether the June 2026 authorisation rules changed this was not confirmed | Reported |
| KYC | Providers must verify the business and its users under the 2026 user-identification rules, hence GST, PAN and signatory checks | Reported |
| Spam rules (TCCCPR) | Apply to calls we make, not calls we answer. A callback counts as outbound | Reported |
| Media location | Plivo and Vobiz both state that a domestic call's media must stay in India | Verified on both sites |
| Data protection (DPDP Act and 2025 Rules) | Main obligations reported to start about 13 May 2027. Phone numbers, transcripts and recordings are personal data | Reported |
| Recording notice | No statute found that prescribes one for a business recording its own calls; announcing it is standard practice | Reported |
| Telling callers it is an AI | No confirmed mandate for inbound calls. Our greeting already says so | Reported |
| Data localisation | Binds the telecom operator, not its business customer. No general rule found for us | Reported |

For Phase 2:

- **Must do:** use an authorised provider and keep the KYC record; route calls only
  through its trunk; keep media in India; keep caller numbers out of logs.
- **Decide before real callers:** transcripts are logged by default
  (`VOICE_LOG_TRANSCRIPTS=true`). During your own test calls that is fine. Before anyone
  else calls, either turn it off or add a short notice to the greeting.
- **Later:** retention and erasure schedule, breach procedure and processor contracts
  (Phases 4 and 5); DLT registration, 140/1600 number series, consent and DND checks
  (Phase 3, outbound only). Note for Phase 3: spam enforcement can suspend every trunk
  on the account, including the inbound one.

## 9. Open questions before any purchase

### 9.1 Blocking: can the free LiveKit plan take India calls?

LiveKit's Plivo guide says calls from a Plivo India number "will fail to connect if
region pinning is not enabled", and Plivo says the far end must be in India. LiveKit's
price list shows "Region pinning" only on the $500/month Scale plan. LiveKit's docs
describe two different mechanisms: protocol-based pinning (Scale plan) and the
`india.sip.livekit.cloud` address for SIP, with no plan stated for the second.

**Ask LiveKit support or the community:** "Is the region-based SIP endpoint
`<subdomain>.india.sip.livekit.cloud` usable on the Build plan?" If the answer is no,
the cheapest route changes and Vobiz or Exotel must confirm they work with the global
endpoint.

Update, 14 November 2026: the owner reports that LiveKit's documentation assistant
describes the India SIP endpoint as separate from the Scale plan's protocol-based
pinning. That matches the docs but is not written confirmation from LiveKit support for
this project, so the question stays open and the plan keeps
`livekit_regional_sip_endpoint` as a blocker.

Also found on 14 November: Plivo's origination URI has `authentication_needed`,
`username` and `password` fields, so inbound authentication is documented but untested.
See `PHASE2_IMPLEMENTATION.md`.

### 9.2 Questions for the chosen provider

| Ask | Plivo | Vobiz |
| :--- | :--- | :--- |
| Exact number rental, setup fee, minimum recharge, GST | Confirm ₹200 vs ₹250; setup fee | All of it |
| Inbound per-minute rate and pulse | Confirm ₹0.38 / 30 s | Yes |
| Is a channel subscription required for one inbound call at a time? | Yes | Yes |
| Hyderabad (040) or any Telangana/AP number available? | Yes | Yes |
| Does inbound to an external SIP address support username and password? | Yes | Confirm |
| SRTP on inbound to an external address | Confirm | Yes |
| Published signalling addresses for an allow-list | Yes | Listed in its docs |
| In what format is the dialled number sent (`+91…` or `0…`)? | Yes | Yes; a community kit reports the `0…` form |
| DoT authorisation number, or the underlying carrier | Yes | Yes |
| Trial or refund terms | India trial route | "All payments final" |
| Which documents exactly | One or two? | Company path |

### 9.3 Decisions for you

1. Approve the checkpoint in section 2 (tag, bundle or remote, branch, `.gitignore`).
2. Which entity will hold the telephony account, and can it provide a GST or
   incorporation certificate? That decides Plivo vs Vobiz as much as price does.
3. Approve step 2a. It needs no purchase and can be built and tested with `plan` and
   mocked calls before any number exists.
4. Whether to include step 2b (agent hang-up) in Phase 2.

## Sources

LiveKit (read directly):

- https://docs.livekit.io/telephony/accepting-calls/inbound-trunk/
- https://docs.livekit.io/telephony/accepting-calls/dispatch-rule/
- https://docs.livekit.io/telephony/accepting-calls/workflow-setup/
- https://docs.livekit.io/telephony/features/secure-trunking/
- https://docs.livekit.io/telephony/features/region-pinning/
- https://docs.livekit.io/deploy/admin/regions/region-pinning/
- https://docs.livekit.io/reference/telephony/sip-participant/
- https://docs.livekit.io/reference/telephony/codecs-negotiation/
- https://docs.livekit.io/telephony/testing/
- https://docs.livekit.io/telephony/start/phone-numbers/
- https://docs.livekit.io/telephony/start/providers/plivo/
- https://livekit.com/blog/building-performant-voice-agents-india
- LiveKit Cloud pricing, via the LiveKit docs server

Plivo:

- https://www.plivo.com/sip-trunking/pricing/in/
- https://www.plivo.com/docs/numbers/rent-india-numbers.md
- https://www.plivo.com/docs/voice-agents/sip-trunking/deploy/calling-in-india.md
- https://www.plivo.com/docs/voice-agents/sip-trunking/integration-guides/livekit.md

Vobiz:

- https://www.vobiz.ai/pricing/
- https://www.vobiz.ai/docs/integrations/livekit
- https://www.vobiz.ai/docs/compliance/india/kyc
- https://www.vobiz.ai/docs/buy-a-phone-number.md
- https://www.vobiz.ai/docs/faq/trial-inbound
- https://www.vobiz.ai/docs/faq/domestic-calling-india
- https://www.vobiz.ai/docs/trunks.md
- https://www.vobiz.ai/docs/concepts/ip-whitelisting.md
- https://www.vobiz.ai/terms
- https://github.com/vobiz-ai/Livekit-vobiz-inbound

Exotel:

- https://docs.exotel.com/dynamic-sip-trunking/overview
- https://docs.exotel.com/dynamic-sip-trunking/exotel-sip-trunking-tls-india
- https://docs.exotel.com/business-phone-system/kyc-verification-indian-accounts
- https://exotel.com/pricing/business-phone-system/
- https://support.exotel.com/support/solutions/articles/3000134145-livekit-and-exotel-sip-trunking-integration-guide
- https://community.livekit.io/t/inbound-sip-calls-from-exotel-not-reaching-agent-503-response/468

Others:

- https://www.twilio.com/en-us/guidelines/in/voice
- https://telnyx.com/release-notes/india-local-voice-numbers
- https://support.telnyx.com/en/articles/3739465-india-did-requirements

Regulation:

- https://trai.gov.in/sites/default/files/2026-07/PR_No91of2026.pdf
- https://www.khaitanco.com/sites/default/files/2026-06/Ergo_Principal%20Telecom%20Authorisation%20Rules_26062026.pdf
- https://trilegal.com/knowledge_repository/new-guidelines-for-other-service-providers-osps/
- https://ksandk.com/data-protection-and-data-privacy/dpdp-act-rules-2026-compliance-deadline/
- https://www.azbpartners.com/bank/indias-digital-personal-data-protection-act-phased-rollout-and-key-compliance-milestones/
- https://www.khaitanco.com/thought-leadership/MeitY-notifies-the-IT-Amendment-Rules-2026
- https://www.corpseed.com/law-update/section-3-7-of-the-telecommunications-act-2023-is-now-in-force
