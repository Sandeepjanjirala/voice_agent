# Dashboard build: progress

Building our own AI-employee calling dashboard on the existing Django project.
See [FEATURE_MATRIX.md](../FEATURE_MATRIX.md) for the comparison that drives it.

| Milestone | Scope | State |
| :--- | :--- | :--- |
| A | Dashboard shell, navigation, Hire Employee wizard | Done, 16 Nov 2026 |
| B | Employee configuration, publishing, voice testing, versions | Done, 16 Nov 2026 (two items moved to D, see below) |
| C | Campuses, leads, conversations, analytics | Done, 16 Nov 2026 |
| D | Employees on outbound calls, inbound routing, authorised campaigns | Done, 17 Nov 2026 (dry-run and switched-off parts listed below) |
| E | Integrations and actions, calendar and WhatsApp, hours and missed callers, jobs, production readiness | Approved for a separate commit, 17 Nov 2026; acceptance and pilot gates in PILOT_READINESS.md |
| Platform improvements | Longer instructions by signed reference, test and business calling, multi-number inbound, pilot blockers 1 to 3, end-to-end workflow | Built and verified on branch `platform-improvements`, 18 Nov 2026; not committed |
| Any job, and every call reviewable | Configuration-driven employees; the conversation and its analysis on each call page | Built and verified on the same branch, 24 Nov 2026; not committed |
| Call recordings | Both sides recorded after consent, encrypted private storage, a player on the call page | Built and verified on the same branch, 24 Nov 2026; awaiting the owner's first real recorded call; not committed |

## Milestone A

**What you can do now.** Sign in as a staff user and land on Home. Open Hire an
employee, describe a job (or start from one of four education examples), and get
an AI-written draft: name, job title, opening line in Telugu-English, purpose,
conversation steps, details to collect, limits, ending and strictness. Edit all
of it, reorder steps, save, draft again, or delete. Drafts are never used on a
call.

**What was built**

- `apps/workforce`: `Employee` model, the configuration schema (`config.py`),
  the LLM client (`llm.py`) and drafting with a template fallback (`drafting.py`).
- Dashboard shell (`apps/core/templates/dashboard/base.html`): sidebar, phone
  drawer, flash messages. Voice studio, Phone calls and Agent profile now sit
  inside it, unchanged.
- Home with real numbers: employees, calls today, 7-day answer rate, median
  response time, recent calls, setup checklist.
- Ten planned sections each have a page stating that they are not built, what
  works today and what is planned. No mock data and no dead buttons.
- Drafting uses the same `LLM_*` settings as the voice worker. Django still
  imports nothing from the voice stack.

**Checks**

| Check | Result |
| :--- | :--- |
| `pytest` | 483 passed (422 before, 61 new) |
| `ruff check`, `ruff format` | Clean |
| Process-boundary test | Passes |
| Migrations | `workforce.0001_initial` applied to the local SQLite database; `makemigrations --check` clean |
| Permissions | Every dashboard URL: anonymous goes to sign-in, non-staff gets 403 (Home sends them to the voice studio), changes need POST and a CSRF token |
| Chromium (`tests/browser/dashboard.cjs`) | Passes at 1440 px and 390 px: all 16 sidebar pages, hire, validation, edit, reorder, save, draft again, delete, non-staff access, no sideways scroll, no console errors |
| Existing browser suites | `calls.cjs` and `voice.cjs` both pass inside the new shell |
| Real LLM | Two real drafting requests through LiveKit Inference: 7.6 seconds together, valid, invented no school facts |

**Not checked**

- The suite was not run against PostgreSQL this time (the local database is SQLite).
- No phone call was placed.

**Changed behaviour to know about**

- Signing in now lands on `/home/`. Non-staff users are passed on to the voice studio.
- `tests/django/test_voice.py`: one assertion updated for that redirect.
- New optional settings: `EMPLOYEE_DRAFT_MODEL`, `LIVEKIT_INFERENCE_URL`.

**Decisions for the owner**

1. Steps are capped at 6 short sentences because they travel in the voice
   agent's per-call prompt (1 kB budget). Longer flows would need staged prompts
   in the voice worker, proposed for Milestone D.
2. Dashboard access is all-or-nothing by the staff flag until Milestone C adds
   campus-level permissions.

## Milestone B

**What you can do now.** Open an employee, edit and save the draft, try it by
voice in your browser, and publish it. Each publish makes a numbered version that
is never edited afterwards. Later edits stay in the draft until you publish again
or discard them. Any earlier version can be copied back into the draft. A
published employee can be paused and switched back on.

**What was built**

- `EmployeeVersion` model, plus `live_version` and `taking_calls` on `Employee`
  (migration `workforce.0002_versions`).
- `apps/workforce/publishing.py`: compiles a configuration into the profile the
  voice worker already accepts, and checks it against the worker's own limits
  before publishing, so nothing is cut silently. **The voice worker was not
  changed.**
- Editor: publish panel with script-size meter, reasons a script cannot be used,
  Publish, Discard changes, Pause / Switch on, and version history.
- Voice studio: `Try the draft` and `Try version N` start a browser conversation
  with that employee's script. Staff only; no phone call is placed.
- Strictness now reaches the voice agent as an instruction.
- Drafting asks the LLM for a shorter script so that it fits the prompt budget.

**Checks**

| Check | Result |
| :--- | :--- |
| `pytest` | 497 passed (14 new) |
| Cross-process test | A published script parsed by the worker's own code arrives unclamped and within its 1 kB prompt budget |
| Migrations | `0002_versions` applied locally; `makemigrations --check` clean |
| Permissions | Publish, discard, restore and switch: staff only, POST only; non-staff cannot try an employee's script |
| Chromium | `dashboard.cjs` passes including try, publish, second version, restore, discard, pause; `calls.cjs` and `voice.cjs` pass |
| `ruff` | **Not run for Milestone B.** Security software on this machine deleted `ruff.exe` three times during the session (the README describes this). The code compiles and long lines were wrapped by hand. Run `uv run ruff format .` and `uv run ruff check .` before committing. |

**Not checked**

- A spoken conversation with a published employee. The token carries the right
  script (tested), but nobody has talked to it by microphone yet. This is the
  owner's check: open an employee, press `Try the draft`, and speak.
- PostgreSQL; no phone call was placed.

**Moved to Milestone D, with reasons**

1. *Phone calls using an employee.* Phone calls still use the Agent profile. Tying
   a call to an employee version belongs with channel assignment.
2. *Per-employee voice (speaker and pace).* The voice is set once in `.env.local`
   and read when the worker starts. Changing it per call needs a voice-worker
   change, which the project rules say to make with a scenario first.

**Known limits**

- Business name and facts still come from the Agent profile page until campus
  knowledge exists (C, E).
- The script-size meter reflects the saved draft, not unsaved typing.

## Milestone C

**What you can do now.** As the platform superuser, set up an organisation with
its campuses and add its staff: organisation admins (everything in that
organisation) and campus staff (only the campuses ticked for them). Add leads by
hand or import a list, which is checked row by row before anything is stored.
Work a lead: stage, owner, notes, follow-ups. Record permission to call, keep a
do-not-call list, erase a contact. See every call under Conversations and the
numbers under Performance, both limited to what your role may see. Admins get an
access log and a retention policy.

**How phone numbers are kept**

- A number is stored three ways and never in the clear: encrypted (Fernet:
  AES with an HMAC, so tampering is detected), as a keyed HMAC-SHA256 used for
  duplicate matching and do-not-call checks, and masked for display.
- The two keys come from `.env.local` (`CONTACT_ENCRYPTION_KEYS`,
  `CONTACT_LOOKUP_KEY`), never from the database, so a database backup alone
  cannot be read. In production they belong in a secrets manager.
- The only code that decrypts is `contacts.number_for_call`. It refuses unless
  permission to call is recorded and the number is not on the do-not-call list,
  it writes an access-log entry, and it is switched off altogether
  (`CONTACT_DIALING_ENABLED = False`) until wider calling is authorised.
- No page, export, log line or access-log entry contains a full number. Exports
  carry masked numbers and are themselves logged.
- `manage.py contactkeys generate | status | rotate | rehash` creates and
  rotates keys. `manage.py retention [--apply]` applies the retention policy.

**What was built**

- `apps/tenancy`: `Organisation`, `Campus`, `Membership` (role and campuses),
  `RetentionPolicy`, `AuditEvent`. `access.py` holds the roles, their named
  permissions and the `Scope` every dashboard query goes through.
- `apps/crm`: `Contact`, `OptOut`, `ContactImport`, `Lead`, `LeadNote`,
  `FollowUp`, with encryption, import, retention and reports.
- Employees and call records now belong to an organisation. Records made before
  organisations existed are adopted by the first organisation created.
- Leads, Conversations, Performance, Campuses & staff and Privacy & settings are
  real pages; five sidebar sections remain marked as planned.

**Checks**

| Check | Result |
| :--- | :--- |
| `pytest` on SQLite | 585 passed (497 before) |
| `pytest` on PostgreSQL 17 | 585 passed, in a disposable container |
| `ruff format --check`, `ruff check` | Clean (ruff 0.17.5, the locked version) |
| `manage.py check`, `makemigrations --check` | Clean; four new migrations applied locally |
| Isolation | Another organisation's or campus's lead, contact, call, task, campus, staff member, employee or do-not-call entry is a 404 by ID; campus and assignee choices posted from outside are rejected; filters and number search cannot cross a boundary |
| Encryption | Round trip, tamper detection, no clear-text number in any table, key rotation (old key readable, re-encrypt, old key removed), lookup-key change repaired by `rehash`, missing key refused |
| Do-not-call | Blocks every campus of the organisation, survives erasure, excluded from imports, does not leak to another organisation |
| Chromium | `crm.cjs` (new) and `dashboard.cjs` pass at desktop and phone width; `calls.cjs` and `voice.cjs` still pass |

**Not checked**

- No real contact data was used anywhere: every name and number is made up.
- Key rotation was tested with test keys, not rehearsed on a populated database.
- `uv.lock` was not regenerated: `uv` is not on the agent's PATH. `cryptography`
  was added to `pyproject.toml` and installed into the venv; run `uv lock`.

**Changed behaviour to know about**

- The dashboard is for members of an organisation and the platform superuser.
  A Django staff account without a membership no longer opens it. Phone calls and
  Agent profile remain platform tools for Django staff.
- New dependency: `cryptography`. New settings: `CONTACT_ENCRYPTION_KEYS`,
  `CONTACT_LOOKUP_KEY`. A local pair was generated into `.env.local`.
- The test settings use a fast password hasher; the suite runs in about 10 seconds.

## Milestone D

**What you can do now**

- Give each AI employee its own voice, speaking speed, language and approved
  actions; publish them as a version.
- Add facts and rules under **Knowledge**, for every campus or one. Each call
  is given its campus's items from the next call, without publishing again.
- On a lead, **Call the test number** rings the approved test number with a
  published employee, as if calling that contact. The contact is never dialled.
- After a call, see the AI summary, the outcome, the answers to the employee's
  questions, the masked transcript (if your organisation keeps transcripts)
  and every action the system took or declined, with the reason.
- Add business lines under **Numbers & channels**, each with a campus and the
  employee that answers it, and see inbound readiness. **Incoming calls** shows
  which number rings whom and every inbound call.
- Create a **campaign** for one campus and one published employee, add
  contacts with permission to call, record the agreement, and run a dry run
  that shows who would be called, when each attempt would be, and who is
  skipped and why.
- Create an **instant-lead source**: a private address and secret for a
  website form. Each lead it receives becomes a consented contact and a lead,
  queued for a call; a dry run shows when it would be called.

**How a call is put together**

`workforce.publishing.plan_call` builds every call from the employee's
published version, the campus's knowledge, the organisation's name and the
lead, and returns a snapshot. The call record stores that snapshot once and
refuses any later change to it, so editing a draft can never alter a call in
progress or rewrite what a past call was told. A draft or paused employee is
never put on a call.

**Voice worker changes** (all opt-in; a job without the new fields behaves as
before, and the existing worker tests are unchanged)

- `profile.py`: reads the profile's voice (speaker, pace, language), who the
  call is with, and whether the transcript may be reported. `voice_for()`
  applies the voice per call; an unknown speaker falls back to the configured
  one and is logged.
- `call_reporting.py`: when allowed, the "ended" report carries the transcript,
  with number-like text masked and its size capped.
- `inbound.py` (new): with `INBOUND_CONTEXT_URL` set, an inbound call asks
  Django, by a signed request, which employee answers the dialled number, then
  reports the call's lifecycle and deletes the room at the end.

**What runs where**

| Piece | Code |
| :--- | :--- |
| Numbers, campaigns, queue, sources, results, actions | `apps/calling` (new) |
| Inbound routing and caller recognition | `apps/calling/inbound.py`, `POST /api/telephony/inbound-context/` |
| Lead intake API | `POST /api/intake/<source id>/`, header `X-Lead-Secret` |
| Dry runs | `apps/calling/queue.py` |
| Summary, outcome and actions | `apps/calling/analysis.py`; `manage.py analyse_calls [--apply]` |
| Knowledge | `apps/workforce/knowledge.py`, `KnowledgeItem` |

**Checks**

| Check | Result |
| :--- | :--- |
| `pytest` on SQLite | 660 passed (598 before this milestone's tests) |
| `pytest` on PostgreSQL 17.11 | 660 passed, in a disposable container; 13 migrations of the five apps apply on a clean database |
| `ruff check`, `ruff format --check` | Clean, ruff 0.17.5 |
| `manage.py check`, `makemigrations --check` | Clean; four new migrations applied to the local database, your 7 call records kept |
| Worker tests | 200 passed, 18 of them new: voice per call, context, transcript masking and size, inbound lookup client |
| Employee version on calls | Draft edits after publishing never reach a call; the snapshot cannot be changed; paused and draft employees are refused |
| Isolation | Campaigns, sources, numbers, knowledge and calls of another organisation or campus are 404; campus staff can view but not manage; posted employees and campuses from outside are rejected |
| Chromium | `calling.cjs` (new) passes the whole flow above, desktop and 390 px; `dashboard.cjs`, `crm.cjs`, `calls.cjs` and `voice.cjs` still pass; no server errors |
| `uv.lock` | Updated with `py -3.12 -m uv lock`: only `cryptography 46.0.7` added; `uv sync --locked --dry-run` matches the environment |

**Not checked, or not possible here**

- No real call was placed. The phone test, rehearsal and inbound paths were
  run against the fake LiveKit gateway and a simulated line.
- The voice worker was not run against LiveKit with the new profile fields.
  The first real check is a rehearsal call: you should hear the chosen voice.
- No LLM was called for analysis in tests: the summary used a canned answer.

**Switched off or dry-run only, on purpose**

- Calling stored contacts (`CONTACT_DIALING_ENABLED = False`). Campaigns and
  instant leads run as dry runs; real calls go only to the approved test number.
- Inbound SIP: routes are ready, but the trunk is not created until it can be
  authenticated (see [INBOUND.md](../INBOUND.md)).
- Transfer to a person: designed in [INBOUND.md](../INBOUND.md), not built.
- Recordings: not available (needs LiveKit egress and storage).

**Changed behaviour to know about**

- An employee now introduces itself with its organisation's name (and campus),
  not the business name on the Agent profile page. Knowledge entries replace
  the Agent profile's business information once any exist.
- Business lines and inbound callers may be landlines (`phones.normalize_line`);
  contacts must still be mobiles.
- Worker reports may be up to 64 kB, to carry a transcript.
- New setting: `INBOUND_CONTEXT_URL` (worker), documented in `.env.example`.

**Decisions for you**

1. **Transcripts** are off by default. Summaries, outcomes and automatic
   follow-ups need them: choose "Use it for the call summary, then delete it"
   or "Keep the transcript" under Privacy & settings.
2. **`livekit-protocol 1.1.31` is yanked upstream** (uv warns: chat history is
   not uploaded to LiveKit observability with agents 1.7.0 to 1.8.8). I did not
   upgrade it, as asked. Upgrading `livekit-agents` is a separate, tested step.
3. **Scheduled jobs**: `analyse_calls --apply` and `retention --apply` need a
   scheduler. A Windows Task Scheduler entry works for one server; Celery and
   Redis are proposed for Milestone E, with a real call queue.

## Milestone E

**What you can do now**

- **Integrations & actions** (admins). Connect:
  - the built-in campus calendar
  - Google Calendar
  - WhatsApp Business (Cloud API)
  - your own REST API
  - signed webhooks

  Then approve actions: check availability, book an appointment, send an
  approved WhatsApp template, make a fixed REST request, update a lead, add a
  follow-up, or hand over to a person. Each action has:
  - its own input schema and validation
  - credentials stored encrypted and never shown again
  - a 10-second timeout and a size cap
  - a logged run with a clear "Done", "Simulated", "Refused" or "Failed"

  An action can only reach its integration's own address. Private addresses
  are refused. The LLM never writes code or chooses a URL.
- **On a lead**, staff run the enabled actions, see what was done and see
  appointments. **On a contact**, record WhatsApp permission, with how and
  when they agreed. It is separate from permission to call.
- **On an employee**:
  - Hear the opening line in the chosen voice and speed, without a phone call.
  - Tick which approved actions it may use after calls.
  - See where it works (campuses, numbers, campaigns, lead sources) and its
    recent calls and actions.
- **On a campus**:
  - Weekly opening hours, and holidays (one campus, or all).
  - Visit length and the closed message.
  - Who is told when a caller asks for a person.
- **Incoming calls** lists missed callers by campus: after hours (a message
  was taken) and lines with nobody ready. Staff mark them "Called back".
- **Notifications** (the bell) cover due follow-ups, missed callers,
  escalations, and failed actions and webhooks, for the right campus or
  person.
- **Operations** (admins) shows:
  - each scheduled job's last run
  - analyses waiting, retrying or given up
  - webhook deliveries
  - failed actions
  - the call queue
  - whether Redis is reachable
- **Usage & costs** (admins) shows calls, talk minutes, summaries, WhatsApp
  messages and voice previews per month, by employee and campus. It estimates
  cost from the rates you set under Privacy & settings.

**Your decisions, as built**

1. **Transcripts: analyse, then delete.** New organisations default to it.
   The transcript is used for the summary and outcome, then deleted.
   - On failure, analysis retries after 2, 4, 8 and 16 minutes, then gives
     up. It deletes the transcript either way.
   - Each call asks LiveKit Cloud to keep nothing (`record=False`), unless
     the organisation turns on "Let LiveKit Cloud keep call recordings…".
   - If turned on, LiveKit is also asked to redact personal details.
   - What LiveKit keeps, where and for how long:
     [DEPLOYMENT.md](../DEPLOYMENT.md#privacy-what-livekit-cloud-keeps).
2. **LiveKit dependency.** `livekit-protocol` moved from 1.1.31 (withdrawn) to
   1.1.32. That is the only change in the lock. `livekit-agents` stays
   1.8.10. The worker tests pass, including a real `AgentSession` started
   with each recording choice.
3. **Redis + Celery + Beat.** One app (`config/celery.py`) and one Beat
   schedule run six idempotent jobs:

   | Job | Every |
   | :--- | :--- |
   | Analyse calls | 2 min |
   | Deliver webhooks | 1 min |
   | Follow-up reminders | 15 min |
   | Campaign eligibility (dry run) | 1 h |
   | Usage report | 1 h |
   | Retention | daily |

   A cache lock keeps each job to one run at a time. Automatic dialling of
   real customers stays off.

**Inbound.** LiveKit's reply of 16 November (community topic 2530) is recorded
in [INBOUND.md](../INBOUND.md):

- The India regional endpoint works on Build, with no approval needed.
- They recommend deploying the worker to `ap-south`.
- Allow-list enforcement is available on request.

The trunk stays unprovisioned until LiveKit confirms enforcement is on and a
call from an unlisted address is rejected. After-hours handling, missed
callers and escalation are built. Live transfer still waits for the trunk.

**Production readiness** (not deployed):

- `deploy/` holds:
  - a web image (gunicorn)
  - Celery worker and Beat
  - PostgreSQL 17 and Redis 7, with no public ports
  - Caddy for automatic HTTPS
  - a nightly backup script
- With DEBUG off, the settings give HTTPS only, secure cookies, HSTS,
  `X-Frame-Options: DENY`, 12-hour sessions, and a readiness check hidden
  from the public.
- See [DEPLOYMENT.md](../DEPLOYMENT.md) and
  [DISASTER_RECOVERY.md](../DISASTER_RECOVERY.md).

**What runs where**

| Piece | Code |
| :--- | :--- |
| Integrations, actions, runs, appointments, webhooks | `apps/integrations` (new) |
| Jobs, notifications, usage, operations, readiness | `apps/operations` (new), `config/celery.py` |
| Opening hours and holidays | `apps/tenancy/hours.py`, `OpeningHours`, `Holiday` |
| Missed callers, after-hours notice | `apps/calling/inbound.py`, `MissedCall` |
| Voice preview | `apps/workforce/preview.py`, `POST /employees/<id>/voice-preview/` |
| Recording choice per call | `voice_agent/profile.py` (`recording_options`) |
| Deployment | `deploy/` |

**Checks**

| Check | Result |
| :--- | :--- |
| `pytest` on SQLite | 721 passed (660 at the end of D) |
| `pytest` on PostgreSQL 17.11 | 721 passed, in a disposable container; every migration applies on a clean database |
| `ruff check`, `ruff format` | Clean on every changed file, ruff 0.17.5 |
| `manage.py check`, `makemigrations --check` | Clean |
| `manage.py check --deploy` | Clean with production settings (run inside the test suite) |
| Celery, Redis and Beat | Real worker and Beat against Redis: Beat sent `deliver-webhooks` and the worker ran it, recorded as a job run |
| Production stack | Built and started locally on loopback only, then removed. All services healthy, HTTP redirects to HTTPS, secure headers and cookies, static files served, backup and restore drill passed (see [DEPLOYMENT.md](../DEPLOYMENT.md)) |
| Chromium | `platform.cjs` (new) covers everything above at desktop and 390 px. It includes a booking, a double-booking refusal, signed webhooks checked at a loopback receiver, a missed caller, a voice preview, staff limits and phone widths. `dashboard.cjs`, `crm.cjs`, `calling.cjs` and `calls.cjs` still pass. No server errors |
| `uv.lock` | Three isolated changes, additions only otherwise: `livekit-protocol` 1.1.31 → 1.1.32; `celery[redis]` (celery 5.7.0, redis 8.1.0 and their dependencies); `gunicorn` 23.0.0. `uv lock --check` passes |

**Bugs found and fixed while checking**

- Approved actions could not be created from the page. The form treated a new
  action as an existing one because its UUID is set before saving.
- Running an action from a lead returned a server error (an audit field-name
  clash).
- "Send a test event" crashed when integrations are live.
- An empty `NAME=` line in `.env.local` turned a switch off instead of keeping
  its default. This would have silently disabled the HTTPS redirect and secure
  cookies.
- `.dockerignore` let `db.sqlite3`, logs and test output into Docker images.

**Changed behaviour to know about**

- A call is analysed automatically once its transcript arrives (a background
  job); "Analyse now" becomes "Analyse again".
- New organisations default to "analyse, then delete" for transcripts, and to
  LiveKit Cloud keeping nothing.
- The organisation settings form also takes the three cost rates; its button
  is now "Save".
- New settings, all in `.env.example`: `REDIS_URL`, `CELERY_TASK_ALWAYS_EAGER`,
  `INTEGRATIONS_LIVE_ENABLED`, `INTEGRATIONS_ALLOW_LOCAL`, and the
  `DJANGO_*` HTTPS and cookie settings.

**Switched off, on purpose**

- `INTEGRATIONS_LIVE_ENABLED=false`: calendar bookings outside the built-in
  calendar, WhatsApp messages, REST requests and webhooks are checked and
  logged as "Simulated", with the exact request; nothing is sent. The built-in
  campus calendar works, because it is this system's own data. Rehearsal calls
  never book, message or call out.
- Calling stored contacts, campaigns and instant leads: still dry runs.
- Inbound trunk: not created (see above). Live transfer: not built.
- Nothing is deployed.

**Not checked, or not possible here**

- No real Google Calendar, WhatsApp, REST or webhook endpoint was used. The
  requests were checked against the providers' documented formats, with fake
  answers.
- No real Sarvam voice preview: the request matches the installed plugin's own
  REST call, and was answered by a fake.
- No real phone call, and no real domain or certificate.

**Decisions for you**

1. **Commit.** Owner approved two separate commits on 17 November 2026.
   D was committed as `881ff2a`, exactly the original 82-file staged tree.
   E includes the consolidated synthetic acceptance gate and readiness review.
2. **Allow-list.** Reply in community topic 2530 asking LiveKit to switch
   enforcement on for `p_44waokrv9lv`. Add `livekit_regional_sip_endpoint` to
   `TELEPHONY_CONFIRMED` in `.env.local` now; add the allow-list item only
   after the rejection test.
3. **Cost rates.** Set them under Privacy & settings for the Usage page's
   estimate.
4. **Before live integrations:** a Google Cloud OAuth client, a Meta WhatsApp
   Business account with approved templates, and an explicit decision to set
   `INTEGRATIONS_LIVE_ENABLED=true`.

## Platform improvements (branch `platform-improvements`, 18 November 2026)

Not committed: waiting for the owner's approval.

**What you can do now**

- **Write longer instructions.**
  - **The old limit is gone.** An employee's instructions may take
    `EMPLOYEE_INSTRUCTION_BYTES` (default 6144 bytes, about 2,000 Telugu
    letters). The old 800-byte metadata limit no longer applies.
  - **How a call gets them.** It carries a signed reference that expires after
    five minutes. The worker fetches the call's fixed snapshot from
    `/api/telephony/call-profile/`, and refuses to dial if it cannot.
  - **The editor and agent settings show the size as you type.** They use the
    server's own count: a Telugu letter takes three bytes, and the strictness
    sentence is included.
- **Make a test call.** It goes only to a number in `OUTBOUND_ALLOWLIST_NUMBERS`,
  even when `OUTBOUND_DIALING_MODE=live`. The page sends the number's position
  in the list, so editing the request cannot reach another number.
- **Make a business call.**
  - **Who:** a contact with recorded permission, or someone new whose
    permission you record.
  - **How:** choose the campus, published employee, caller ID and purpose. The
    next page shows every check, and the call is placed only after you confirm.
    The server checks again then.
  - **Checks:** consent, the do-not-call list, 09:00 to 21:00 and the campus's
    hours, the employee and campus, the caller ID, the monthly minutes budget,
    one call at a time, and the dialing policy.
  - **Record:** every attempt is recorded with its reason.
  - **Dialling:** with `CONTACT_DIALING_ENABLED=false` (the default), the checks
    run and nobody is dialled.
- **Set a caller ID** per business line under Numbers & channels. It needs
  two ticks: "Use as the caller ID", and "The provider confirmed it".
  Optionally, give the line its own outbound trunk.
- **Inbound:**
  - Every routed business line joins the one secure trunk, alongside the
    comma-separated `TELEPHONY_INBOUND_NUMBER` numbers.
  - The dispatch rule is bound to that trunk, with a new room for each call.
  - A call with no published employee assigned hears one neutral line
    (`INBOUND_UNROUTED_MESSAGE`), and LiveKit is asked to keep nothing. The
    same happens when Django cannot be reached.

**Pilot blockers fixed in code** (details in PILOT_READINESS.md)

- **Inbound fallback:** fails closed, as above.
- **Outgoing requests:**
  - Redirects are never followed.
  - The host is resolved once, and every address it resolves to is checked.
  - The connection goes to that checked address, with TLS verified against
    the real host name. This stops DNS rebinding.
- **Bookings:**
  - A database rule allows one booking per campus slot, under a per-campus
    lock.
  - Google bookings check free/busy first, use an idempotent event ID and must
    get that ID back.
  - A timeout leaves the booking "not confirmed", and a retry confirms it.
- **Webhooks:** switching one off cancels its queued deliveries, and each
  delivery re-checks the switch when it is sent.
- **Found while testing:** calendar event IDs in action results were partly
  masked as phone numbers. They are now kept whole.

**Added later (18 and 24 November)**

- **No login page on your own computer.** With `DEV_AUTO_LOGIN_USER=<username>`
  in `.env.local`, browsers on this computer open the dashboard signed in
  (`apps/core/middleware.py`):
  - It works only with `DJANGO_DEBUG=true`, only from 127.0.0.1 or ::1, and
    never for `/api/`.
  - Django refuses to start with it when `DJANGO_DEBUG=false`.
- **Tests no longer read `.env.local`.** Before this, every test run and browser
  QA server loaded the developer's real LiveKit keys, Sarvam key, test number
  and caller ID. No test used them, but one relied on them by accident.
  `SKIP_ENV_LOCAL` now stops both Django and the worker reading the file under
  the test settings.
- **The agent no longer claims to have noted or passed on details.** The agent
  debugger showed the no-employee agent saying "I've noted your details" (5 of
  5 runs) and "I'll pass your details to the office team" (1 of 5) when the
  caller had given none. The prompt rule now reads "You cannot book, check,
  note, save, fix, tell or ask anyone anything". Afterwards, 10 of 10 runs only
  said the office team will call back. Scenario `SCN_mPqQGeFaLkWS` in
  `scenarios.yaml` covers it. The prompt is now at 2,303 of its 2,304 bytes.
- **The owner's local database** had the four migrations applied, after a copy
  was saved in `backups/` (git-ignored).

**How it was checked**

- **Python suite:**
  - SQLite: 879 passed, 1 skipped. The skipped test is the four-thread booking
    race, which needs PostgreSQL.
  - PostgreSQL 17: 880 passed.
  - Not touched by this branch: `test_intake_is_rate_limited` had failed once
    on PostgreSQL. Its 61 requests could cross a clock minute and reset the
    per-minute counter. Its clock is now fixed.
- **Lint and project checks:** ruff check and format, Django `check`,
  `makemigrations --check`, `uv lock --check` and `uv sync --locked --dry-run`
  are all clean.
- **Workflow test:** `tests/django/test_platform_workflow.py` drives the whole
  workflow through the dashboard:
  - hire
  - long Telugu instructions
  - knowledge and an approved action
  - publish
  - campus and caller ID
  - a business call that fetches its profile by signed reference
  - the worker's reports
  - the analysed result
  - an inbound call on the same line
- **Simulations:** the inbound SIP calls are simulated in
  `tests/django/test_inbound_simulation.py` and
  `tests/voice/test_inbound_fallback.py`.
- **Browser suites:** dashboard, crm, calling, platform and calls all pass.
  - `calls.cjs` checks that a raw number sent in place of a position is
    refused.
  - It also checks that a position edited in the page is refused.
  - `dashboard.cjs` checks that the live size equals the server's.
- **Screenshots:** `tests/browser/screens.cjs` captures 25 screens at 1440 px
  and 390 px. None scrolls sideways or shows a full contact number.
- **Agent debugger** (text mode, real LLM through LiveKit Inference, no room or
  phone):
  - Each scenario in `scenarios.yaml` was replayed turn by turn, and each
    still behaves as its expectation says.
  - The instructions with no employee are byte-for-byte the same as before
    this branch.
- **Migrations:**
  - `telephony/0006_agent_profile_limits`
  - `integrations/0002_booking_safety_and_cancel`
  - `calling/0004_business_calling`
  - `tenancy/0006_business_calling`

**Not checked, or not possible here**

- No real call, booking, message or webhook. LiveKit, Google, WhatsApp and the
  LLM were fakes in the test suites. The agent debugger used the real LLM.
- **Simulations could not run** on 24 November. LiveKit Cloud refused them:
  the project has used this billing period's simulation turns
  ("MaxSimulationTextTurns"). More turns need a plan upgrade (owner's
  decision), or the next billing period. Until then the scenarios were checked
  by hand in the agent debugger instead.
- **Found, not fixed:** after a plain "thanks", the agent often asks "anything
  else?" (ఇంకేమైనా help కావాలా?), which the style rules forbid. It did so in 4
  of 5 runs before the prompt change and 3 of 5 after, so it predates it.
  Fixing it needs prompt bytes that are not left (2,303 of 2,304).
- No fresh side-by-side comparison with the reference product. REA needs
  Chrome's debugging port (9222), which was not reachable this session. The
  design follows the Milestone A review.
- Inbound provisioning stays blocked:
  - LiveKit has not yet confirmed allow-list enforcement.
  - Vobiz has not yet confirmed its signalling address ranges and SRTP.
  - The negative test (a call from an unlisted address is rejected) has not
    been run.
- Whether Vobiz lets a business line be shown as caller ID on outbound calls
  is not yet confirmed. That is why the "The provider confirmed it" tick
  exists.

## Any job, and every call reviewable (24 November 2026)

Same branch, not committed.

**What was wrong**

- **One cautious receptionist for every job.** Every call, whatever its
  employee, got the same base rules: "do not ask for a name, number or details"
  and "say only that our office team will call back". An employee set up to
  collect feedback or check details was told two opposite things on every
  turn.
- **Things the form could not say.** It had no place for when to hand over,
  what success means, how to handle situations, or which results a call can
  have.
- **Knowledge rules were only in a lookup**, so the employee might never see
  them.
- **Facts from another organisation.** Employees without Knowledge fell back to
  the Agent profile page's facts. That page is one setting for the whole
  installation, so the facts could come from another organisation.
- **The analysis knew nothing about the job.** It used one fixed list of
  outcomes for every employee, and an appointment field for everyone.
- **Little to review after a call:**
  - By default the conversation was deleted once a summary of at most 1,000
    characters existed.
  - When kept, it had no times, cut-offs, tools or end reason, and nobody was
    told when it went.
- **The end-of-call report could be lost:**
  - It was sent once, with a 2-second timeout.
  - It was sent with escaped Unicode (6 bytes for a Telugu letter), so a
    long Telugu call could exceed Django's 64 kB limit and be refused whole.

**What changed**

- **The prompt has two modes.** With no employee, the cautious rules stay. With
  an employee, "do what This call says, ask what it asks for, raise no other
  topic".
- **New employee fields**, carried through the editor, drafting, publishing,
  the size budget (mirrored exactly by the live meter) and each call's
  snapshot:
  - what a successful call achieves
  - situations and what to do
  - when to hand over
  - possible results
  - information it may give
- **Knowledge.** Rules now reach the prompt (whole rules, up to 1 kB).
  Employees use only their own organisation's Knowledge.
- **The worker records the conversation as it happened**, when the
  organisation allows: times, cut-offs, speech-recognition confidence, tool
  names (never their data) and why the call ended. The report is UTF-8, capped
  by bytes, and retried. Django accepts it once, so a retry never restores a
  deleted conversation.
- **The analysis reads each call against that employee's job.** It records the
  reason, topics, what the AI said, what was collected, resolution, the
  employee's own result, open items, next step, follow-up, instruction-
  following and issues.
  - An issue or note is kept only if its quote is in the conversation.
  - Measured issues come from the call's own data.
  - Follow-up is also decided by the system: requests, open items, failed
    actions, errors.
- **The call page shows all of this**, with a chat-style conversation labelled
  as speech recognition. It says what was kept or when it was deleted. Opening
  a conversation is written to the access log.
- **Conversations** can be filtered by "needs follow-up", "has issues" and the
  employees' own results. Lead pages show each call's result and next step.
- **Recordings** are evaluated in RECORDINGS.md and not built.
- **Local testing.** `CONSOLE_PROFILE_FILE` lets console and debugger sessions
  play any employee.

**How it was checked**

- **Python suite:**
  - SQLite: 911 passed, 1 skipped.
  - PostgreSQL 17: 912 passed.
- **`tests/django/test_conversation_review.py`:**
  - Three different employees (fee desk, feedback, records check) are set up
    through the editor and called through the signed endpoints.
  - Each prompt holds only its own job.
  - Each analysis is given its own job and conversation.
  - Invented quotes are dropped.
  - Off, summary-then-delete and keep each behave as described.
  - A retried report does not restore a deleted conversation.
  - Other organisations and campuses get 404, and viewing is logged.
- **`tests/voice/test_conversation_record.py`:** the prompt modes, the new
  fields, the transcript details, the end reasons, retries, and a long Telugu
  report under 64 kB.
- **Real LLM, no call** (synthetic Telugu and English conversations):
  - The analysis caught a forbidden discount promise and quoted it.
  - It found a repeated question in the feedback call.
  - It read a changed emergency contact as "Details changed".
- **Agent debugger, text mode:**
  - Each of the three employees did its own job.
  - None brought up appointments.
  - The fee desk answered from its own information and followed its discount
    situation.
- **Browser:** all suites pass. The calling suite checks the chat, summary,
  pending items, measured and quoted issues, and that the panel is not cut off
  at 1440 and 390 px.

**Found, not fixed**

- With some questions (here a fee), the agent writes English words such as
  "class" and "fee" in Telugu script, against the style rule. This happened in
  most runs, with or without these changes, including with the original
  prompt. Callers hear the same words either way; only the transcript reads
  differently.
- After a plain "thanks" the agent often asks "anything else?", against the
  style rules. This existed before.

**Needs a live test**

- A real phone call, to see the conversation and end reason reported by the
  real worker.
- Recording, which needs your decision and a storage bucket.

## Call recordings (24 November 2026)

Same branch, not committed. Details and setup are in RECORDINGS.md.

**What you can do now**

- **Switch it on.** Under Privacy & settings, set the organisation to record
  never (the default), approved test calls only, or any call, and write the
  question the AI employee asks.
- **Consent.** On an allowed call the question follows the opening line. Only a
  clear yes starts recording; no, an unclear answer, or a later "please don't"
  means nothing is kept.
- **Listen.** Under Conversations → a call → Recording:
  - play, pause, a seek bar and times
  - the person on the left ear, the AI on the right
  - click a line of the conversation to play from there
  - download (admins)
  - the status, with the reason when there is nothing to play

**How it was built**

- **The worker** attaches LiveKit's own session recorder (`RecorderIO`) at
  consent and uploads the Ogg file to Django when the call ends. The upload is
  signed and retried, and the local copy is always deleted.
- **Django:**
  - It accepts audio only for a call it allowed and whose person agreed.
  - It encrypts the file (Fernet) and stores it locally or in private S3 in
    India.
  - It streams the audio through ten-minute personal links, logs openings and
    downloads, and deletes recordings by their own retention setting and with
    their call.
- **New permissions:** `calls.recordings` and `calls.recordings_download`.
- **A new command:** `manage.py recordings key|status|check`.
- **A new dependency:** `boto3`, locked.
- **Migrations:** `tenancy/0007_call_recordings` and
  `calling/0006_call_recordings`.

**How it was checked**

- **Python suite:** SQLite 936 passed, 1 skipped; PostgreSQL 17, 937 passed.
- **The worker test** runs LiveKit's real recorder on synthetic audio and
  decodes the file:
  - the person's tone on the left, the AI's on the right
  - a reply cut off at 0.5 s stops at 0.5 s
  - the upload signed exactly as Django checks it
- **The Django tests** cover:
  - which calls may be recorded, outbound and inbound
  - consent, refusal, failure, and audio that never arrives
  - refused uploads: no consent, not allowed, bad signature, stale, not audio,
    a duplicate
  - encrypted storage and byte-range playback
  - links that are personal, expire, and cannot be turned into downloads
  - campus and organisation isolation
  - the access log
  - retention, and deletion with the call
  - S3 server-side encryption, with a stubbed client
- **The calling browser suite** uploads a real Ogg file as the worker would and
  plays it in Chromium: play, pause, seek, click-to-play from a line of the
  conversation, and download. It also checks the 390 px layout.
- **A bug found by the tests and fixed:** the handler that deletes a
  recording's file with its call was dropped by Python's garbage collector. It
  is now held strongly.

**Needs a live test (the owner's)**

- **A real recorded call to the approved number.** The recorder has run on
  synthetic audio, never on a phone line.
- **S3 in India.** Needs a bucket and credentials; the code is tested with a
  stubbed client.

## Simple everyday flow and call lists (24 November 2026)

Same branch, not committed. The owner found the dashboard confusing and the
calls page limited to one fixed number.

**What you can do now**

- **Four steps, in order.** Home and the sidebar lead with the everyday flow:
  AI employees → Make calls → Calls → Call results, with a steps bar at the
  top of each of those pages. Settings sits under the four steps.
- **Everything else under More.** Leads, campuses, campaigns, numbers,
  knowledge, integrations, the test line and the rest are folded into a "More"
  section in the sidebar. Nothing was removed.
- **Make calls.**
  - Type numbers, one per line with an optional name, or upload an Excel
    (`.xlsx`) or CSV file with a Phone or Mobile column and an optional Name
    column. Files without a header row work too.
  - **Check numbers** lists rows that are not Indian mobile numbers, with the
    reason, and keeps repeated numbers once. Numbers on the do-not-call list
    are skipped.
  - Calling starts only after a tick that everyone agreed to be called. The
    wording of that confirmation is stored with the list.
  - The chosen AI employee (or the Agent profile) calls one person at a time.
    Each row shows its status live and links to the call's result. Pause,
    resume and cancel work at any time.
  - The usual rules still apply to every call: 9 AM to 9 PM, campus hours,
    one call at a time, the monthly minutes budget, and the dialing policy
    (`OUTBOUND_DIALING_MODE`).
  - Lists hold up to 500 rows; uploads up to 2 MB. Numbers are stored
    encrypted and shown masked.
- **Background jobs without Redis.** `CELERY_TASK_ALWAYS_EAGER` now defaults
  to on when `REDIS_URL` is empty. Before, jobs were sent to a Redis no worker
  read, so call results never appeared on the owner's machine.

**How it was built**

- `apps/calling/calllists.py` reads, checks, stores and advances a list. The
  open list page asks for the next step every 3 seconds. Celery beat does the
  same every 15 seconds (`operations.advance_call_lists`), so calls continue
  when Celery runs and the page is closed.
- The database constraint that allows one active outbound call makes sure two
  steps can never dial at once.
- **New models:** `CallList` and `CallListEntry`
  (`calling/0007_call_lists`).
- **A new dependency:** `openpyxl`, locked.

**How it was checked**

- **Python suite:** SQLite 948 passed, 1 skipped; PostgreSQL 17, 949 passed.
- **`tests/django/test_call_lists.py`** covers:
  - typed lines and Excel and CSV uploads, with or without headers
  - the 500-row cap
  - the do-not-call list
  - that no call starts without the tick
  - calls one after another with the employee's profile
  - waiting outside calling hours
  - pause and cancel
  - the Agent profile
  - organisation and campus isolation
  - the sidebar order
  - the Redis default
- **`tests/browser/make_calls.cjs`** runs the whole flow in Chromium:
  - typed numbers plus an Excel upload
  - the check and the refusal without the tick
  - two calls on a simulated line
  - the result page
  - no sideways scrolling at 390 px
- The other browser suites were updated for the new sidebar and pass.

**Needs a live test (the owner's)**

- A real list of two of the owner's own numbers, to confirm that the second
  call starts after the first ends on a real phone line.

## Unused pages deleted, Settings redesigned (25 November 2026)

Same branch, not committed. The owner asked to delete the pages that were not
used or only simulated, and found the Settings page hard to read.

**Deleted at the owner's choice** (pages, their code and tests; the database
tables stay, so old records are kept):

- **Voice studio** and its token API. `/` now opens Home. "Try the draft" and
  "Call the test number" on an employee are replaced by **Make calls**, which
  opens Make calls with that employee chosen.
- **Test line**, its business-call pages and the per-line caller ID choice
  (nothing else used it). The staff JSON API at `/api/telephony/calls/` stays
  for the automated tests; every call goes out from the caller ID in
  `.env.local`.
- **Agent profile**: the page, the form and the fallback that made calls
  without an AI employee use it. Make calls always needs an employee; an older
  list without one pauses instead of calling.
- **Campaigns** and **Instant leads** (they only ran dry runs), with the intake
  API, the queue, `run_campaign_call` and the eligibility job.
- **Integrations & actions**: calendar, WhatsApp, REST and webhook actions (all
  simulated), their pages, the webhook job, and approved actions on employees
  and leads. The built-in after-call actions (follow-up task, lead stage)
  remain. `transport.py` and `safety.py` stay: the voice preview uses them.

**Kept under More**: Leads & contacts, Campuses & staff, Performance, Incoming
calls, Numbers & channels, Knowledge, Usage & costs, Operations.

**Settings**: one column of sections (Organisation, Calls & privacy, Budget,
Do-not-call list, Data retention, Access log), each with a short explanation
beside it and a jump bar at the top. The form lost two fields that did nothing:
the WhatsApp message cost and "calls at the same time".

**How it was checked**

- Python suite: SQLite 813 passed; PostgreSQL 17, 813 passed. The count fell
  because the tests of deleted features went with them.
- Browser suites dashboard, crm, calling, make_calls, screens and platform pass.
  `calls.cjs`, `voice.cjs` and `live_smoke.cjs` were deleted with their pages.
- Django check, `makemigrations --check` and `uv lock --check` are clean; no
  migration was needed.

## Run the browser check

```powershell
.\.venv\Scripts\python.exe -m tests.browser.dashboard_server   # window 1; Ctrl+C when done
node tests/browser/dashboard.cjs                               # window 2
node tests/browser/crm.cjs                                     # window 2, same server
```

For Milestone D, restart the server and run `node tests/browser/calling.cjs`,
then `node tests/browser/make_calls.cjs` and `node tests/browser/screens.cjs` on
the same server. `screens.cjs` captures every screen at 1440 px and 390 px into
`browser-artifacts/screens/`. For Milestone E, restart it again and run
`node tests/browser/platform.cjs`. The server refuses every outside request.

The server uses a disposable database, a canned LLM answer, a simulated phone
line and made-up people. Restart it before running the pair again.
