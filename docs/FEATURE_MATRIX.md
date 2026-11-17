# Feature matrix: reference product vs. this project

Compares the reference AI-calling product (Outpero) with this Django project, to
decide what to build. We build our own implementation with our own design and
wording; nothing was copied from the reference's code, assets or branding.

Last updated: 16 November 2026, after Milestone C.

## How the reference was studied

| Source | What it covered | How |
| :--- | :--- | :--- |
| Published docs (`docs.outpero.com`) | All 27 help topics and the API overview | REA browser scenario in a temporary browser profile, no sign-in |
| Live app (`app.outpero.com`) | 12 screens: Home, All employees, Hire, one employee's Call script tab, Leads, Conversations, Performance, Campaigns, Instant leads, Incoming calls, Numbers, Billing, Settings | REA in the owner's own signed-in tab: sidebar navigation only, page text and structure only |

Not done, on purpose: no clicks on anything that calls, hires, publishes, buys or
saves; no network bodies, storage, cookies or script sources captured; the
Developers page (API keys) was not opened; emails and phone numbers were masked
before the captures were read. The employee's Instant, Training, Actions,
Outcomes, Voice and Settings tabs, and the sub-tabs of Instant leads, Incoming
calls, Billing and Settings, were not opened: the docs describe them.

**Evidence column:** `Docs` = stated in the published documentation. `Seen` =
observed on the live screen. `Assumed` = our inference; treat as a guess.

**Status column:** `Done` = built and tested here. `Partial` = some of it exists.
`None` = not started. The letter is the milestone that will deliver the rest.

## 1. Home dashboard

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Setup checklist | "2 of 6 done" steps towards the first real call: test call, publish, credits, number, start | Seen | Done (A): four checks computed from real configuration | Add publish, campus and channel steps as they are built (B–D) |
| Quick links | Open employee, all employees, add credits, get number, test call, hire | Seen | Done (A): sidebar plus Hire button on every page | – |
| Live activity | "0 on calls · 0.0/min" in the top bar | Seen | None | Active-call indicator from `CallRecord` (D) |
| Insight dashboard | After going live: reads every call, ranks leads, says what needs attention | Seen (locked), detail Assumed | Partial (C): employees, open leads, follow-ups due, calls today, answer rate, response time, recent calls | Lead ranking needs call summaries (E) |
| Notifications bell | Campaign finished, opt-outs, messages from callers, results | Docs, Seen | None | Notification model and bell (E) |
| Assistant ("Ask Swara"), tour, search, dark theme | Present in the top bar | Seen | None | Not planned; revisit after E |

## 2. AI employees

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Employee list | Cards with status, role, channel, voice, language, number, calls and leads today | Seen | Done (C): list with status, role, kind of calls, language; per-employee leads and calls on Performance | – |
| Filters | Tabs All / Live / Paused / Draft, search, job filter, sort | Seen | Done (B): search, kind of calls, status | – |
| Lifecycle | Draft → In training → Live → Paused | Docs | Done (B): Draft, Published, Paused | – |
| Three-part gate | Real calls need a number, "Taking calls" on, and one publish | Docs | Partial (B): publish and the taking-calls switch exist | Enforce the gate when calls use employees and channels (D) |
| One job per employee | Instant, Bulk or Inbound; switchable later | Docs | Done (A): one kind of calls per employee, editable while in draft | Rules for switching after publish (B) |

## 3. Hire an employee

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Describe the job in plain language | One text box; a draft is generated | Docs, Seen | Done (A): LLM writes name, role, opening line, purpose, steps, details to collect, limits, ending, strictness | – |
| Channel and language first | Instant / Bulk / Inbound; 7 languages plus "natural spoken" | Docs, Seen | Done (A): three kinds of calls; Telugu + English, Telugu, English | More languages only if needed |
| Industry templates | 14 industry starting points | Seen | Done (A), differently: four education example briefs that fill the box | Add examples per sector later; they are data, not code |
| Describe by voice | Record a voice note instead of typing | Docs, Seen | None | Browser recording to Sarvam STT (E) |
| Paste a script from another employee | Offered on the hire page | Seen | None | Duplicate-employee action (B) |
| Free test call before confirming | Call your own phone to hear the draft | Docs | Partial (B): try the draft by voice in the browser | Phone test call with the employee on the approved number (D) |
| No invented facts | – (not stated) | – | Done (A): the draft lists facts it needs instead of inventing them | Feed those facts into knowledge (E) |
| Works without the AI | – | – | Done (A): plain template with a visible notice | – |

## 4. Employee configuration and testing

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Tabs | Overview, Instant, Call script, Training, Actions, Outcomes, Voice, Settings | Seen | Partial (A): one editor page | Split into sections as they are built (B–E) |
| Opening line | Spoken word for word on connect | Docs, Seen | Done (A) | – |
| Call steps | Named steps; add, delete, reorder; up to 40,000 characters | Docs, Seen | Done (A), deliberately smaller: up to 6 one-sentence steps, because they travel in the voice agent's per-call prompt | Longer flows need staged prompts in the voice worker (D) |
| Always-on FAQs step | Cannot be removed | Docs, Seen | None | Covered by the knowledge lookup tool (E) |
| Call end rules | Built-in rule plus one extra rule per line | Docs, Seen | Done (A): one ending field | – |
| Strictness dial | 1 (flexible) to 5 (strict) | Docs, Seen | Done (B): sent to the voice agent as an instruction | – |
| Pre-call variables | `{{placeholders}}` filled from the lead | Docs, Seen | None | Variables from lead and campaign data (D) |
| Captured variables | Details extracted from the call | Docs | Partial (A): "details to collect" list | Extraction after the call (C) |
| Change the script by instruction | "Tap and say your change" | Seen | Partial (A): "Draft again" from the original brief | Instruction-based edits (B) |
| Voice | Tiers, preview, ambient background sound | Docs | Partial: one voice set in `.env.local` | Per-employee Sarvam voice and pace; needs a voice-worker change (D) |
| Rehearse in text chat | Chat with the employee, no phone | Docs, Seen | Partial (B): voice rehearsal in the browser | Text rehearsal (E) |
| Test call | One-off call to a typed number | Docs, Seen | Done (D): "Call the test number" on a published employee uses that version and records it; the approved number only | Typed numbers stay unavailable until wider dialling is authorised |
| Publish | Script, actions and webhooks need "Apply to live calls"; voice and training apply at once | Docs | Done (B): Publish and Discard changes; publishing is blocked with reasons if the script cannot be used | – |
| Versions | Last 3 published versions, restorable | Docs | Done (B): every version kept, last 5 shown, any can be copied into the draft | – |
| Start from scratch / switch channel | Destructive resets in Settings | Docs | Partial (A): Draft again, Delete | Channel switch rules (B) |
| Max call length | 10 s to 1 hour, default 10 min | Docs | Partial: one global limit in `.env.local` | Per-employee limit (D) |

## 5. Leads

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| One row per person | All calls to a number roll up into one lead | Docs | Done (C), split in two: a contact per number per organisation, with any number of leads | – |
| Views | Pipeline, list, cards, priority | Docs, Seen | Partial (C): list with stage counters | Board view later |
| Filters and export | Channel, employee, source, date, outcome, hot; CSV export | Seen | Done (C): stage, campus, owner, search by name, subject or full number; contact export with masked numbers, admins only, logged | Lead export (E) |
| Lead detail | Call history, everything learned, notes, Call now | Docs | Done (C): stage, owner, AI employee, notes, follow-up tasks, linked calls | Call now, once stored contacts may be dialled (D) |
| Counters | Total, qualified, pending call-back, hot | Seen | Done (C): a counter per stage, follow-ups due | – |

## 6. Conversations

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Call log | Contact, employee, channel, outcome, talk time, cost, summary | Docs, Seen | Done (C, D): masked number, result, talk time, campus, employee version, who placed it | AI summary and outcome (E) |
| Filters | Date, direction, recording, how it ended, talk time, channel, outcome, employee, source; remembered | Docs, Seen | Done (C): period, result, direction, campus | Employee filter (D) |
| Call detail | Recording, summary, sentiment, transcript, extracted details, notes, share | Docs | Partial (C): timings, phone-network result, turns, latency, staff notes, live status and End call | Summary and collected details; recording and transcript need a consent decision (E) |
| Export CSV | From the filtered view | Docs, Seen | Done (C): masked numbers, admins only, logged | – |

## 7. Performance

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Headline numbers | Calls, connect rate, conversion, average length, success rate, spend, vs. previous period | Seen | Done (C): calls, answer rate, talk time, response time, new leads, converted | Comparison with the previous period |
| Charts | Calls over time, funnel, outcomes, by channel, busiest hours | Docs, Seen | Partial (C): calls per day, how calls ended, leads by stage | Busiest hours, by channel (D) |
| Leaderboard | Per employee, exportable | Docs, Seen | Done (C): per campus and per AI employee, CSV export | – |
| Filters | 7/30/90 days, custom, employee, channel, issues only, minimum duration | Docs, Seen | Partial (C): 7/30/90 days, campus | Custom range, employee |
| Latency reporting | – (not in the reference) | – | Done (C): per call, and as a median on Performance and Conversations | – |

## 8. Campaigns

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Create | Name, employee, contacts (manual, paste, CSV), column mapping, reusable lists | Docs | None | (D) |
| Rules | Window, days, holidays, concurrency 1–20, retries 1–9, schedule or repeat | Docs | None | Start with one call at a time (D) |
| Run | Pause, resume, retry, cancel, export; live panel | Docs | None | Celery and Redis for the queue (D) |
| Limits | 10 live campaigns; one per number, the next queues | Docs | Partial: one outbound call at a time, enforced by a database constraint | (D) |
| Do-not-call | Account list plus per-campaign suppression, checked before each call | Docs | Done (C): organisation-wide list, enforced where a stored number is released for a call and in imports | Wire into campaigns when they exist (D) |
| Consent confirmation | Recorded when a campaign is launched | Docs | Done (C): per contact, with evidence, who and when; per import, with an attestation | Per campaign (D) |

Real dialling stays limited to the one approved test number until wider calling is
authorised, so campaigns will run in dry-run mode first.

## 9. Instant leads

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Intake address | Private URL per employee with a secret header; only `phone` is required | Docs | None | Signed intake endpoint (D) |
| Sources | Webhook, Google Sheets, Meta lead ads through Zapier, Zapier/Make | Docs | None | Webhook first (D) |
| Calling policy | Call delay, window, out-of-window handling, retry ladder up to 10 attempts | Docs | None | (D) |
| Test and activity | Test lead without a call; activity log of accepted, duplicate, rejected | Docs, Seen | None | (D) |
| Sections | Activity, Analytics, Queue, Sources; hot-lead list | Seen | None | (D) |

## 10. Incoming calls

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Receptionist employee | Answers every call to its number | Docs | Partial: inbound trunk planning and caller detection are built and tested, not activated | Secure trunk authentication is the blocker (D) |
| Setup checklist | Number, receptionist, attach | Seen | None | Provider-independent routing table: which employee answers which number (D) |
| Sections | Overview, Calls, Messages, Missed callers | Seen | None | (D) |
| Recording notice, opening hours, call back missed callers | Described | Docs | None | (D) |
| Leads calling back | The outbound employee answers its own call-backs | Docs | None | Later than D |

Inbound SIP routing stays off until it can be authenticated securely.

## 11. Numbers and channels

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Buy a number | Search, pay, KYC, auto-activate | Docs, Seen | Not planned: numbers are bought from the provider (Vobiz) by the owner | – |
| Assign a number | One number per employee | Docs | None | Channel records and assignment (D) |
| Concurrent calls | "Channels" per number | Docs, Seen | Partial: one call at a time | (D) |
| Calling rules shown | Consent, hours, do-not-call, responsibility | Docs, Seen | None | Show our own rules on the page (D) |
| Line status | – | – | Partial: Phone calls shows readiness and blockers for the test line | Move to this page (D) |

## 12. Knowledge and integrations

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Facts and rules | Typed, spoken or photographed; sorted into facts and rules; reviewed before saving | Docs | Partial: one "business information" text behind a lookup tool, on the Agent profile | Per-employee and per-campus facts and rules (E) |
| Documents | Uploaded and consulted only when needed | Docs | None | (E) |
| Suggestions from real calls | Moments where the employee struggled | Docs | None | Needs transcripts (after E) |
| Actions | Calendar, Sheets, custom API, WhatsApp; each opt-in per employee | Docs | None | Approved-action registry with per-employee permission (E) |
| Result webhooks | Every call, completed only, voicemail only; test send | Docs | None | (E) |
| Public API | `/v1` calls, campaigns, leads, do-not-call, employees; dry run | Docs | None | (E) |

## 13. Usage, credits and settings

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Prepaid credits | Per-minute billing, top-ups, statements | Docs, Seen | Not planned: this is our own deployment, not a resold service | Usage report (minutes per employee and campus) instead (E) |
| Business settings | Name, default language | Docs, Seen | Done (C): organisation name, campuses, staff and roles | Default language per organisation |
| Caller memory and call-backs | Off by default; remembers callers, books call-backs | Docs, Seen | None | Only with a stated privacy basis (E) |
| Do-not-call list | Add singly or in bulk; auto-added on request | Docs, Seen | Done (C): add a number, opt a contact out, remove (admins) | Auto-add when a caller asks during a call (D) |
| Privacy and data | Export, remembered callers, credentials | Seen | Done (C): encrypted numbers, access log, retention policy, erase a contact, masked exports | Scheduled retention job (E) |
| Notification preferences | Bell and pop-up per event type | Seen | None | (E) |

## Where we differ on purpose

- **Organisations and campuses.** The reference has one business per account. We
  have organisations, campuses and per-campus staff (done), and will add
  campus-specific knowledge (E). Nothing in the shared code names a school or a
  kind of enquiry: lead stages and contact origins are general.
- **Do-not-call everywhere.** The reference applies its list to bulk campaigns
  only. We will check it before every outbound call.
- **Short steps.** Latency matters more to us than long scripts; steps stay short
  and reference facts sit behind a lookup tool.
- **No billing.** We report usage; we do not sell credits.
- **One approved number.** Real calls stay limited to the test number until the
  owner authorises more.
