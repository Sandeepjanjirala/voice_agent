# Feature matrix: reference product vs. this project

Compares the reference AI-calling product (Outpero) with this Django project, to
decide what to build. We build our own implementation with our own design and
wording; nothing was copied from the reference's code, assets or branding.

Last updated: 17 November 2026, after Milestone D.

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
| Setup checklist | Number, receptionist, attach | Seen | Done (D): readiness checks on Numbers and Incoming calls | – |
| Quick links | Open employee, all employees, add credits, get number, test call, hire | Seen | Done (A): sidebar plus Hire button on every page | – |
| Live activity | "0 on calls · 0.0/min" in the top bar | Seen | None | Active-call indicator from `CallRecord` (E) |
| Insight dashboard | After going live: reads every call, ranks leads, says what needs attention | Seen (locked), detail Assumed | Partial (C): employees, open leads, follow-ups due, calls today, answer rate, response time, recent calls | Lead ranking needs call summaries (E) |
| Notifications bell | Campaign finished, opt-outs, messages from callers, results | Docs, Seen | None | Notification model and bell (E) |
| Assistant ("Ask Swara"), tour, search, dark theme | Present in the top bar | Seen | None | Not planned; revisit after E |

## 2. AI employees

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Employee list | Cards with status, role, channel, voice, language, number, calls and leads today | Seen | Done (C): list with status, role, kind of calls, language; per-employee leads and calls on Performance | – |
| Filters | Tabs All / Live / Paused / Draft, search, job filter, sort | Seen | Done (B): search, kind of calls, status | – |
| Lifecycle | Draft → In training → Live → Paused | Docs | Done (B): Draft, Published, Paused | – |
| Three-part gate | Real calls need a number, "Taking calls" on, and one publish | Docs | Done (D): a call needs a published, switched-on employee; inbound also needs a routed number | – |
| One job per employee | Instant, Bulk or Inbound; switchable later | Docs | Done (A): one kind of calls per employee, editable while in draft | Rules for switching after publish (B) |

## 3. Hire an employee

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Describe the job in plain language | One text box; a draft is generated | Docs, Seen | Done (A): LLM writes name, role, opening line, purpose, steps, details to collect, limits, ending, strictness | – |
| Channel and language first | Instant / Bulk / Inbound; 7 languages plus "natural spoken" | Docs, Seen | Done (A): three kinds of calls; Telugu + English, Telugu, English | More languages only if needed |
| Industry templates | 14 industry starting points | Seen | Done (A), differently: four education example briefs that fill the box | Add examples per sector later; they are data, not code |
| Describe by voice | Record a voice note instead of typing | Docs, Seen | None | Browser recording to Sarvam STT (E) |
| Paste a script from another employee | Offered on the hire page | Seen | None | Duplicate-employee action (E) |
| Free test call before confirming | Call your own phone to hear the draft | Docs | Done (B, D): try the draft in the browser; call the test number with the published version | – |
| No invented facts | – (not stated) | – | Done (A): the draft lists facts it needs instead of inventing them | Feed those facts into knowledge (E) |
| Works without the AI | – | – | Done (A): plain template with a visible notice | – |

## 4. Employee configuration and testing

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Tabs | Overview, Instant, Call script, Training, Actions, Outcomes, Voice, Settings | Seen | Partial (A): one editor page, now with success, situations, hand-over, possible results (the reference's Outcomes) and information it may give | Split into sections |
| Opening line | Spoken word for word on connect | Docs, Seen | Done (A) | – |
| Call steps | Named steps; add, delete, reorder; up to 40,000 characters | Docs, Seen | Done (A, platform improvements), deliberately smaller: up to 12 steps; all instructions together up to `EMPLOYEE_INSTRUCTION_BYTES` (6144 bytes by default, about 2,000 Telugu letters), shown live as you type; the call fetches them by signed reference | Longer flows need staged prompts in the voice worker |
| Always-on FAQs step | Cannot be removed | Docs, Seen | None | Covered by the knowledge lookup tool (E) |
| Call end rules | Built-in rule plus one extra rule per line | Docs, Seen | Done (A): one ending field | – |
| Strictness dial | 1 (flexible) to 5 (strict) | Docs, Seen | Done (B): sent to the voice agent as an instruction | – |
| Pre-call variables | `{{placeholders}}` filled from the lead | Docs, Seen | Partial (D): the contact's name and the lead's subject reach the call | Custom variables from campaign columns (E) |
| Captured variables | Details extracted from the call | Docs | Done (A, 24 Nov): "details to collect", read after each call, plus other important details the person gave | – |
| Change the script by instruction | "Tap and say your change" | Seen | Partial (A): "Draft again" from the original brief | Instruction-based edits (B) |
| Voice | Tiers, preview, ambient background sound | Docs | Done (D, E): per-employee Sarvam voice, speed and language, applied per call; a preview speaks the opening line with the chosen voice, no call placed | Background sound |
| Rehearse in text chat | Chat with the employee, no phone | Docs, Seen | None: the browser voice rehearsal (Voice studio) was removed on 25 Nov 2026 at the owner's request | Hear an employee by calling your own number from Make calls |
| Test call | One-off call to a typed number | Docs, Seen | Done (25 Nov): Make calls with a typed number, or **Make calls** on the employee's page with it already chosen | – |
| Publish | Script, actions and webhooks need "Apply to live calls"; voice and training apply at once | Docs | Done (B): Publish and Discard changes; publishing is blocked with reasons if the script cannot be used | – |
| Versions | Last 3 published versions, restorable | Docs | Done (B): every version kept, last 5 shown, any can be copied into the draft | – |
| Start from scratch / switch channel | Destructive resets in Settings | Docs | Partial (A): Draft again, Delete | Channel switch rules (B) |
| Max call length | 10 s to 1 hour, default 10 min | Docs | Partial: one global limit in `.env.local` | Per-employee limit (E) |

## 5. Leads

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| One row per person | All calls to a number roll up into one lead | Docs | Done (C), split in two: a contact per number per organisation, with any number of leads | – |
| Views | Pipeline, list, cards, priority | Docs, Seen | Partial (C): list with stage counters | Board view later |
| Filters and export | Channel, employee, source, date, outcome, hot; CSV export | Seen | Done (C): stage, location, owner, search by name, subject or full number; contact export with masked numbers, admins only, logged | Lead export (E) |
| Lead detail | Call history, everything learned, notes, Call now | Docs | Done (C): stage, owner, AI employee, notes, follow-up tasks, linked calls | Call now, once stored contacts may be dialled; rehearsal on the test number works (D) |
| Counters | Total, qualified, pending call-back, hot | Seen | Done (C): a counter per stage, follow-ups due | – |

## 6. Conversations

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Call log | Contact, employee, channel, outcome, talk time, cost, summary | Docs, Seen | Done (C, D): masked number, result, talk time, location, employee version, who placed it | AI summary and outcome (E) |
| Filters | Date, direction, recording, how it ended, talk time, channel, outcome, employee, source; remembered | Docs, Seen | Done (C, D): period, result, direction, location, AI employee, outcome | Remembered filters |
| Call detail | Recording, summary, sentiment, transcript, extracted details, notes, share | Docs | Done (D, 24 Nov): reason, summary, the employee's own result, resolution, collected details, what the AI said, open items, next step, follow-up, instruction-following, measured and quoted issues, the conversation as a timed chat (if kept), how it ended, actions, notes | Sentiment. Recording: done (24 Nov), after consent, encrypted, with a player; awaiting the first real recorded call |
| Export CSV | From the filtered view | Docs, Seen | Done (C): masked numbers, admins only, logged | – |

## 7. Performance

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Headline numbers | Calls, connect rate, conversion, average length, success rate, spend, vs. previous period | Seen | Done (C): calls, answer rate, talk time, response time, new leads, converted | Comparison with the previous period |
| Charts | Calls over time, funnel, outcomes, by channel, busiest hours | Docs, Seen | Partial (C): calls per day, how calls ended, leads by stage | Busiest hours, by channel (E) |
| Leaderboard | Per employee, exportable | Docs, Seen | Done (C): per location and per AI employee, CSV export | – |
| Filters | 7/30/90 days, custom, employee, channel, issues only, minimum duration | Docs, Seen | Partial (C): 7/30/90 days, location | Custom range, employee |
| Latency reporting | – (not in the reference) | – | Done (C): per call, and as a median on Performance and Conversations | – |

## 8. Campaigns

Campaigns (dry runs only) were removed on 25 Nov 2026 at the owner's request. **Make calls** does list
calling for real:

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Create | Name, employee, contacts (manual, paste, CSV), column mapping, reusable lists | Docs | Done (25 Nov): typed numbers or an Excel/CSV upload, a published employee, what the calls are about, an optional location | Column-to-variable mapping; reusable lists |
| Rules | Window, days, holidays, concurrency 1–20, retries 1–9, schedule or repeat | Docs | Partial: 09:00–21:00, the location's opening hours and holidays, one call at a time | Retries, schedule and repeat |
| Run | Pause, resume, retry, cancel, export; live panel | Docs | Done (25 Nov): live list page, pause, resume, cancel | Retry and export |
| Limits | 10 live campaigns; one per number, the next queues | Docs | Partial: one outbound call at a time across the installation, enforced by the database; the monthly minutes budget | Simultaneous calls |
| Do-not-call | Account list plus per-campaign suppression, checked before each call | Docs | Done: checked when the list is made and again before each call | Per-list suppression |
| Consent confirmation | Recorded when a campaign is launched | Docs | Done (25 Nov): a tick before calling starts, its wording stored with the list | – |

## 9. Instant leads

Removed on 25 Nov 2026 at the owner's request: the intake address only ran dry runs. Leads can still be added by hand
or imported under Leads & contacts.

## 10. Incoming calls

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Receptionist employee | Answers every call to its number | Docs | Done (D), switched off: routed number, published employee, caller recognised from contacts | Secure trunk authentication is the blocker |
| Setup checklist | Number, receptionist, attach | Seen | Done (D): readiness checks on Numbers and Incoming calls | – |
| Sections | Overview, Calls, Messages, Missed callers | Seen | Done (D, E): overview, calls and missed callers with "Called back" | Messages, once inbound is live |
| Recording notice, opening hours, call back missed callers | Described | Docs | Done (E): opening hours and holidays per location; after hours the employee takes a message; missed callers listed and notified | Recording notice, if recordings are ever kept |
| Leads calling back | The outbound employee answers its own call-backs | Docs | None | (E) |

Inbound SIP routing stays off until it can be authenticated securely.

## 11. Numbers and channels

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Buy a number | Search, pay, KYC, auto-activate | Docs, Seen | Not planned: numbers are bought from the provider (Vobiz) by the owner | – |
| Assign a number | One number per employee | Docs | Done (D, platform improvements): a number has a location and an employee; an employee may answer several; every routed number joins the one secure inbound trunk; a verified number can be a location's caller ID on business calls | – |
| Concurrent calls | "Channels" per number | Docs, Seen | Partial: one outbound call at a time across the installation (a database rule). Each organisation can already store a calls-at-once limit (1 to 20, under Privacy & settings) for when more are authorised | Lifting the one-call rule, with real runs |
| Calling rules shown | Consent, hours, do-not-call, responsibility | Docs, Seen | Done: on Make calls, Numbers & channels and each call list | – |
| Line status | – | – | Done (D): outbound readiness and inbound readiness on Numbers & channels | – |

## 12. Knowledge and integrations

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Facts and rules | Typed, spoken or photographed; sorted into facts and rules; reviewed before saving | Docs | Done (D): typed facts and rules, per location or organisation-wide, used from the next call | Spoken or photographed input (E) |
| Documents | Uploaded and consulted only when needed | Docs | None | (E) |
| Suggestions from real calls | Moments where the employee struggled | Docs | None | Needs transcripts (after E) |
| Actions | Calendar, Sheets, custom API, WhatsApp; each opt-in per employee | Docs | Partial: built-in follow-up task and lead stage only. Calendar, WhatsApp and REST actions were removed on 25 Nov 2026 at the owner's request (they were simulated) | Real integrations, when authorised |
| Result webhooks | Every call, completed only, voicemail only; test send | Docs | Removed on 25 Nov 2026 at the owner's request | – |
| Public API | `/v1` calls, campaigns, leads, do-not-call, employees; dry run | Docs | None | (E) |

## 13. Usage, credits and settings

| Feature | Reference behaviour | Evidence | Our status | Missing work |
| :--- | :--- | :--- | :--- | :--- |
| Prepaid credits | Per-minute billing, top-ups, statements | Docs, Seen | Not planned: this is our own deployment, not a resold service. Done instead (E): Usage & costs, by month, employee and location, with an estimate from your rates | – |
| Business settings | Name, default language | Docs, Seen | Done (C): organisation name, locations, staff and roles | Default language per organisation |
| Caller memory and call-backs | Off by default; remembers callers, books call-backs | Docs, Seen | None | Only with a stated privacy basis (E) |
| Do-not-call list | Add singly or in bulk; auto-added on request | Docs, Seen | Done (C, D): added automatically when a caller asks during a call, where their number is known | Bulk add |
| Privacy and data | Export, remembered callers, credentials | Seen | Done (C, E): encrypted numbers, access log, retention policy run daily by the scheduler, erase a contact, masked exports, transcripts analysed then deleted, LiveKit Cloud asked to keep nothing | – |
| Notification preferences | Bell and pop-up per event type | Seen | Partial (E): bell and list for follow-ups, missed callers, escalations and failures, by location or person | Per-person preferences |

## Where we differ on purpose

- **Organisations and locations.** The reference has one business per account. We
  have organisations, locations and per-location staff (done), and will add
  location-specific knowledge (E). Nothing in the shared code names a school or a
  kind of enquiry: lead stages and contact origins are general.
- **Do-not-call everywhere.** The reference applies its list to bulk campaigns
  only. We will check it before every outbound call.
- **Short steps.** Latency matters more to us than long scripts; steps stay short
  and reference facts sit behind a lookup tool.
- **No billing.** We report usage; we do not sell credits.
- **One approved number.** Real calls stay limited to the test number until the
  owner authorises more.
