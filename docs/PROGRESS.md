# Dashboard build: progress

Building our own AI-employee calling dashboard on the existing Django project.
See [FEATURE_MATRIX.md](FEATURE_MATRIX.md) for the comparison that drives it.

| Milestone | Scope | State |
| :--- | :--- | :--- |
| A | Dashboard shell, navigation, Hire Employee wizard | Done, 16 Nov 2026 |
| B | Employee configuration, publishing, voice testing, versions | Done, 16 Nov 2026 (two items moved to D, see below) |
| C | Campuses, leads, conversations, analytics | Done, 16 Nov 2026 |
| D | Employees on outbound calls, inbound routing, authorised campaigns | Done, 17 Nov 2026 (dry-run and switched-off parts listed below) |
| E | Integrations and actions, calendar and WhatsApp, hours and missed callers, jobs, production readiness | Approved for a separate commit, 17 Nov 2026; acceptance and pilot gates in PILOT_READINESS.md |

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
  authenticated (see [INBOUND.md](INBOUND.md)).
- Transfer to a person: designed in [INBOUND.md](INBOUND.md), not built.
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
     [DEPLOYMENT.md](DEPLOYMENT.md#privacy-what-livekit-cloud-keeps).
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
in [INBOUND.md](INBOUND.md):

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
- See [DEPLOYMENT.md](DEPLOYMENT.md) and
  [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md).

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
| Production stack | Built and started locally on loopback only, then removed. All services healthy, HTTP redirects to HTTPS, secure headers and cookies, static files served, backup and restore drill passed (see [DEPLOYMENT.md](DEPLOYMENT.md)) |
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

## Run the browser check

```powershell
.\.venv\Scripts\python.exe -m tests.browser.dashboard_server   # window 1; Ctrl+C when done
node tests/browser/dashboard.cjs                               # window 2
node tests/browser/crm.cjs                                     # window 2, same server
```

For Milestone D, restart the server and run `node tests/browser/calling.cjs`.
For Milestone E, restart it again and run `node tests/browser/platform.cjs`. It
starts a webhook receiver on 127.0.0.1:8768 and refuses any other address.

The server uses a disposable database, a canned LLM answer, a simulated phone
line and made-up people. Restart it before running the pair again.
