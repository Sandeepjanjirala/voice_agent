# Dashboard, AI employees and call records

How your team uses the Django dashboard, and how call records get there. Setup is in the [README](../README.md).

The workspace is industry-neutral. Set your organisation name, add its locations
or branches, and provide approved facts under Knowledge. Hiring examples cover
new enquiries, customer support, front-desk calls and authorised follow-up; a
custom brief can describe any other supported business workflow. The selected
employee's profile determines the conversation, without a shared institution or
industry assumption.

Locations can be offices, shops, service centres or other business sites. Existing
database fields, role values and URLs containing `campus` are retained for
compatibility; the dashboard presents them as locations. User accounts and
historical call records keep their existing access rules.

## Browser code

Dashboard interactions use jQuery 4.1.0: selectors, events, dynamic form rows,
page updates and AJAX requests. The full build is served locally from
`apps/core/static/vendor/jquery/`, including its MIT license. The base template
loads it once, before the application scripts; no CDN is required. The small
theme initializer runs before CSS to restore a saved theme without a bright flash.

Native browser APIs still handle dialogs, audio, form validation, UTF-8 byte
counts and mutation observation. CSS handles animations. jQuery is a JavaScript
library, so the source files retain their `.js` extension. Django continues to
render the pages and process requests.

Run the disposable `tests.browser.dashboard_server` for browser checks. The
`assistant.cjs` suite verifies the local dependency, single sends, input
composition, safe text rendering and AJAX error/timeout recovery. The existing
dashboard, branding, workflow, calling, make-calls, CRM and platform suites cover
the remaining interactions. Their server uses fake providers and never places
real calls.

### Appearance and motion

`brand/workspace.css` is the shared visual theme across the dashboard, forms,
tables, assistant and sign-in page. It uses light surfaces, violet controls and
lavender, pink and peach accents inspired by the published
[Campus Helper design](https://dribbble.com/shots/27014047-University-Campus-Helper-Chatbot-App-UI-Design).
Light mode is the default. The header's sun/moon switch saves the user's choice
under `vaani-theme` in browser local storage and syncs it between open tabs.
The switch remains usable if storage is blocked, but that choice cannot persist.

The larger home banner preserves the stars, drifting beam and glowing horizon.
Its Pause motion control and the operating system's reduced-motion preference
still apply. The next-step card below it continues to use real workspace state.

Run `node tests/browser/theme.cjs` to check theme persistence and contrast. After
populating the disposable fixture with the platform/calling browser suites, run
`node tests/browser/screens.cjs` to review the main and available detail pages in
both themes at desktop and phone widths. Captures go to
`browser-artifacts/screens/light/` and `browser-artifacts/screens/dark/`.

## Dashboard and AI employees (staff only)

The sidebar keeps five daily destinations: **Home**, **AI employees**, **Make
calls**, **Call results** and **Settings**. **Workspace tools** contains only
**Leads & contacts**, **Knowledge** and **Incoming calls**. Setup and reporting
live under **Settings → Setup & reports**, with an explanation beside each
link: locations and team, phone numbers, performance, usage and operations.
Links and records still follow the user's organisation and location permissions.

**Add a lead** starts with the contact's name, phone number, location and enquiry.
Notes, assignments and calling permission expand when needed. A lead can be saved
without calling permission; its permission remains **Not recorded** and customer
calling stays blocked until it is recorded. To rehearse with a sample lead, an
admin can open that lead and use **Call the test number**, which rings the
configured approved test number with the sample context.

Signing in as a staff user opens <http://127.0.0.1:8002/home/>: your AI employees,
this week's calls and a setup checklist. **Hire an employee** takes a job described
in plain language and drafts an editable employee for that job, whatever it is:
fee questions, feedback, reminders, checking details, reception, support. The
draft has:

- a name and opening line
- why it is calling and what a successful call achieves
- steps and details to collect
- situations and what to do in each
- limits, when to hand over to a person, and how to end
- the possible results of a call (your own labels)
- information it may give

Nothing about any one kind of call is built in: the employee does what its form
says. Edit it, then **Publish**. Each publish is a numbered version; older
versions can be copied back into the draft. **Make calls** on a published
employee opens Make calls with that employee chosen, to call your own number
and hear it.

Drafting uses the same `LLM_*` settings as the voice worker, so it needs no extra
key. If the LLM cannot be reached, the draft starts from a plain template and says so.

### Organisations, locations and roles

| Role | Sees and does |
| :--- | :--- |
| Platform superuser (Django superuser) | Creates organisations; works in one at a time, chosen in the sidebar |
| Organisation admin | Everything in their organisation: locations, team, employees, leads, reports, access log, retention |
| Location team member | Leads, contacts, calls and reports of the locations ticked for them |

On first sign-in the superuser is asked to set up an organisation; existing
employees and call records move into it. Add team members under **Settings → Locations & team**.
A record in another organisation or location is a 404, whatever ID is typed.

### Contacts and phone numbers

Contact numbers are encrypted before they reach the database, matched through a
keyed hash, and shown in full to signed-in users within their organisation and
location permissions. Contact and call-result exports include full numbers.
Logs and audit entries keep their redaction. Older calls that retained only a
masked value show “Number unavailable” when no original can be recovered.
Create the two keys once and add them to `.env.local`:

```powershell
.\.venv\Scripts\python.exe manage.py contactkeys generate   # prints the two lines
.\.venv\Scripts\python.exe manage.py contactkeys status
.\.venv\Scripts\python.exe manage.py contactkeys rotate     # after putting a new key first
.\.venv\Scripts\python.exe manage.py retention              # preview; add --apply to carry out
```

Keep the keys out of Git and out of database backups; in production use a secrets
manager. Calling stored contacts is switched off (`CONTACT_DIALING_ENABLED=false`).
Use made-up contacts until real data is authorised.

### Who can be dialled

One dialing policy, read by Django and the voice worker alike:

| Setting | Meaning |
| :--- | :--- |
| `OUTBOUND_DIALING_MODE=allowlist` (default) | Only numbers in `OUTBOUND_ALLOWLIST_NUMBERS` (comma-separated, +91…). Anything else is refused with a message, before LiveKit is touched |
| `OUTBOUND_DIALING_MODE=live` | Any valid number, between 09:00 and 21:00 and, for a location, in its opening hours. Not to be used until real outbound calling is approved |
| `CONTACT_DIALING_ENABLED=true` | A lead's **Call** button may dial a stored contact who agreed to be called; the policy above still applies. Off (the default): nobody is dialled from a lead. Make calls does not use this switch: it has its own consent tick |

Make calls applies its own checks to every number on a list: the do-not-call
list, 09:00 to 21:00 and the location's opening hours, one call at a time, the
monthly minutes budget (under **Settings**), and the dialing policy above.

Every call goes out from the outbound caller ID set in `.env.local`. Restart
Django and the worker after changing these settings.

### Numbers, knowledge and results

- **Settings → Numbers & channels**: business lines, each with a location and the published
  employee that answers it. Inbound calls stay off until the SIP trunk can be
  authenticated; see [docs/INBOUND.md](INBOUND.md).
- **Knowledge**: facts and rules per location, given to each call.
- After a call, the conversation is analysed against that employee's own job
  by a background job, which then carries out the employee's permitted
  actions (a follow-up task, a lead's stage). Each call page (**Call results**)
  shows:
  - why the call happened and a summary
  - the result (one of the employee's own results) and whether it was resolved
  - what was collected, and what the AI told the person
  - what is still open, the next step, and whether staff must follow up
  - whether the AI followed its instructions
  - problems: those measured from the call (cut-offs, long waits, repeats,
    unclear speech, failed tools), and those read from the conversation,
    which are shown only with a quote that is really in it
  - the conversation itself as a chat with times, marked as what speech
    recognition heard, and how the call ended

  What is kept follows the organisation's setting under Settings:
  - Off: nothing.
  - Summary, then delete (the default): the conversation goes once the summary
    is written, and the page says when.
  - Keep: the conversation is also kept for the retention days.

  Opening a conversation is written to the access log. Conversations can be
  filtered to calls that need follow-up or have issues. **Analyse again**
  reruns the analysis by hand.

### Call recordings

Off until an admin chooses it under Settings: never (the default), demo test
calls without a question, approved test numbers after consent, or any call
after consent. Demo mode requires both a test/rehearsal action and an approved
test destination; ordinary Make calls and inbound calls do not qualify.
Consent modes ask in the opening line and capture nothing without a clear yes.
In either mode, a request to stop discards the audio. Demo playback is labelled
as an approved test and never claims verbal consent.

- **What is recorded:** both sides of the real call audio, with the person on
  the left channel and the AI on the right.
- **Storage:** encrypted with `RECORDING_ENCRYPTION_KEYS`, in a private folder
  or a private S3 bucket in India.
- **Playback:** on the call page, with play, seek, the transcript linked to the
  audio, and download for admins. Every access is logged.
- **Retention:** recordings are deleted on their own schedule, separate from
  transcripts.

Setup and the steps for a first test call are in
[docs/RECORDINGS.md](RECORDINGS.md); `manage.py recordings key|status|check`
helps.

### Locations and operations

- **Locations**: opening hours, holidays, the closed message and who takes
  escalations. **Incoming calls** lists missed callers.
- **Operations**, **Usage & costs** and the notification bell show jobs,
  failures and monthly usage.

For call lists to keep going with the page closed, and for jobs on a schedule,
run Redis and Celery in two more windows:

```powershell
# REDIS_URL=redis://127.0.0.1:6379/0 in .env.local, with Redis running
.\.venv\Scripts\python.exe -m celery -A config worker --loglevel INFO --pool solo
.\.venv\Scripts\python.exe -m celery -A config beat --loglevel INFO
```

Without `REDIS_URL`, jobs run inside Django (the request that queues them, or
the open Make calls page), so call results and call lists work with no Redis.

## Call records and the worker

Every call, including `manage.py telephony call`, is stored as a record with a
full number (when available), timestamps, talk time, SIP status and latency, and appears under
**Call results**.

Make your account staff once (or use a superuser):

```powershell
.\.venv\Scripts\python.exe manage.py shell -c "from django.contrib.auth import get_user_model as U; U().objects.filter(username='YOUR_USERNAME').update(is_staff=True)"
```

`CALL_EVENTS_URL` and `CALL_EVENTS_SECRET` in `.env.local` let the worker report each
call's outcome, SIP status and latency to Django (signed; no numbers or transcripts).
Without them calls still work and outcomes are inferred from LiveKit, with less detail.

With `CALL_EVENTS_URL` set, a call no longer carries the employee's instructions
in its LiveKit metadata. It carries a signed reference that expires after five
minutes, and the worker fetches the call's fixed instructions from
`/api/telephony/call-profile/` with the same secret. If the worker cannot fetch
them, it does not dial. `CALL_PROFILE_BY_REFERENCE=false` turns this off.

### PostgreSQL

SQLite remains the zero-setup default. To use PostgreSQL locally with Docker Desktop:

```powershell
# In .env.local: POSTGRES_PASSWORD=<a strong password>
docker compose --env-file .env.local up -d
# In .env.local: DATABASE_URL=postgres://telugu_agent:<that password>@127.0.0.1:5432/telugu_agent
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
```

Accounts do not move between databases, so create your user again after switching.

## Dashboard AI assistant

The floating AI button opens Vaani Assist on the right. It is closed by default.
This is a Django-only, read-only helper using LangGraph and Gemini, separate from
LiveKit and employee drafting. It can explain workflows and draft text; it cannot
read records or perform actions. Only submitted chat text is sent to Gemini.

Set `GEMINI_API_KEY` in the ignored `.env.local`. `GEMINI_ASSISTANT_MODEL` defaults
to `gemini-3.1-flash-lite`; select a model enabled for your Google project if needed.
Restart Django after changing configuration. No credential reaches the browser.

Chat context is kept in memory for the current page only (the last three exchanges,
up to 2,000 characters per message). New chat clears it. Requests require a signed-in
workspace member and CSRF, accept a bounded JSON body, and allow one request per
user every five seconds. Use the existing shared Redis cache for consistent rate
limits across production processes. Provider calls use a 15-second timeout and retry once on connection failures or
HTTP 502/503/504, within the browser's 45-second deadline. Authentication and quota
errors are not retried. Safe logs distinguish connection, timeout and reply errors.

Checks: `uv run python -m pytest tests/django/test_assistant.py`; browser checks:
run the disposable dashboard QA server and `node tests/browser/assistant.cjs`.
Browser tests mock Gemini and never use the developer's key.

## Guided workspace and call review

The home page recommends a next action from the current workspace state. The
employee, call preparation, call progress and results pages explain their next
step before the detailed controls. Workflow navigation describes each stage;
visiting a later page does not mark earlier stages as completed.

Call review places the summary, recording and conversation first, with notes and
context alongside them. Technical diagnostics are collapsed until opened. The
section shortcuts reveal diagnostics when needed. Transcript bubbles use distinct
speaker colours and readable text in both themes, including Telugu text.

Run `node tests/browser/workflow.cjs` against a fresh disposable
`tests.browser.dashboard_server` to check transcript contrast, notes, next-action
guidance, section links and layouts at 390, 768 and 1440 pixels. The fixture uses
made-up conversations; these checks do not place real calls.
