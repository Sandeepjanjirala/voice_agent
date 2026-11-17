# Dashboard build: progress

Building our own AI-employee calling dashboard on the existing Django project.
See [FEATURE_MATRIX.md](FEATURE_MATRIX.md) for the comparison that drives it.

| Milestone | Scope | State |
| :--- | :--- | :--- |
| A | Dashboard shell, navigation, Hire Employee wizard | Done, 16 Nov 2026 |
| B | Employee configuration, publishing, voice testing, versions | Done, 16 Nov 2026 (two items moved to D, see below) |
| C | Campuses, leads, conversations, analytics | Done, 16 Nov 2026 |
| D | Employees on outbound calls, inbound routing, authorised campaigns | Started: outbound test call with a published version works; inbound routing and campaigns are next |
| E | Knowledge, API actions, integrations, production readiness | Not started |

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

## Milestone D (started)

**Works now.** On a published, switched-on employee, **Call the test number**
places one real call to `OUTBOUND_TEST_ALLOWED_NUMBER` with that version's
script. The call record names the employee version, and the call page follows
it live (ringing, answered) and can end it. Later edits to the draft do not
reach the call. Nothing else can be dialled from the dashboard.

Checked with the fake gateway in `pytest` and with a simulated line in
Chromium. **Not checked with a real phone call:** that needs you to press the
button.

**Still to do in D**

1. Inbound: a routing table (which published employee answers which number),
   independent of the provider. It stays inactive until the SIP trunk can be
   authenticated securely.
2. Campaigns and instant leads, in dry-run mode until wider dialling is authorised.
3. Per-employee voice (speaker and pace), which needs a voice-worker change.
4. Linking a call to a lead when it is placed from one.

## Run the browser check

```powershell
.\.venv\Scripts\python.exe -m tests.browser.dashboard_server   # window 1; Ctrl+C when done
node tests/browser/dashboard.cjs                               # window 2
node tests/browser/crm.cjs                                     # window 2, same server
```

The server uses a disposable database, a canned LLM answer, a simulated phone
line and made-up people. Restart it before running the pair again.
