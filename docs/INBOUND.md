# Inbound calls: routing, lifecycle and transfer

How an incoming phone call reaches the right published AI employee, what is
built, and what stays switched off.

## Current setup and next steps

On 27 November 2026 the owner confirmed that the Vobiz account has only its
trial number. A read-only LiveKit check found no inbound trunks or dispatch
rules. Vobiz trial numbers are outbound-only; a verified account and a purchased,
fully provisioned incoming number are required. Follow
[VOBIZ_SETUP.md](VOBIZ_SETUP.md) for the manual steps and exact local commands.

The application already handles number-to-employee routing, authenticated
worker lookups, incoming call records, transcripts and missed callers. The
worker's local callbacks now point to Django on port 8002. LiveKit Cloud
receives SIP; the worker running on this PC calls Django over loopback, so this
local arrangement does not require exposing Django to the internet.

`manage.py telephony plan` reports local configuration blockers. A ready plan
does not verify the provider or provision anything. `telephony status` reads
the remote objects but cannot prove that LiveKit enforces the IP allowlist.
The first actual incoming call and provider authentication checks remain manual.

## Historical LiveKit support response (16 November 2026)

Our question is in the LiveKit Community thread
[topic 2530](https://community.livekit.io/t/2530), posted 14 November 2026 for project
`p_44waokrv9lv`. Darryn (LiveKit staff) answered on 16 November 2026. The
thread was written when Plivo was the first choice; the LiveKit answers do not
depend on the provider.

| Question | LiveKit's answer | What it means here |
| :--- | :--- | :--- |
| Can the India regional SIP endpoint be used on the Build plan? | Yes. Region-based telephony endpoints are available on all plans. Protocol-based region pinning is the Scale-only feature, and not the one we need. | `livekit_regional_sip_endpoint` is confirmed. The owner adds it to `TELEPHONY_CONFIRMED` in `.env.local`. |
| Is any project-level approval needed? | No. He recommends deploying the agent to `ap-south` so calls stay in the region. | Nothing to request. Deploy the worker to `ap-south` when it is deployed (not yet). |
| Can `allowed_addresses` be enforced on a Build project? | It "CAN be enabled if needed, just ask in this thread". | Not switched on yet. The owner is asking for it in the same thread. |
| Username and password on inbound? | Believed supported. | Vobiz's documented origination configuration has no digest credentials; this setup uses an enforced IP allowlist. Confirm any alternative with the provider. |

This historical response is not proof that enforcement is currently enabled.
Obtain written confirmation for the current project before adding
`livekit_allowed_addresses_enforced` to `TELEPHONY_CONFIRMED`. After the intended
trunk is configured, verify both the allowed provider path and rejection from
an unlisted source using LiveKit's supported test procedure. A rejection while
there are no trunks proves nothing about IP filtering. Keep public routing off
until verification is complete.

Still to come from Vobiz: a number, its signalling addresses (three are
documented), and SRTP on inbound calls (`provider_inbound_srtp`).

## Routing

| Piece | Where |
| :--- | :--- |
| Business lines, their campus and their employee | Numbers & channels page, `apps/calling/models.PhoneNumber` |
| Which employee answers, now | `apps/calling/inbound.route_for`: the number is routed, its employee is published and switched on, and both belong to the same organisation |
| The worker's question | `POST /api/telephony/inbound-context/`, signed with `CALL_EVENTS_SECRET` |
| The worker's side | `voice_agent/inbound.py`, checks every real room and resolves SIP callers through `INBOUND_CONTEXT_URL` |

A number that is not routed answers 404. **The worker then fails closed**
(18 November 2026): the caller hears one neutral line
(`INBOUND_UNROUTED_MESSAGE`, which names no organisation and promises
nothing), LiveKit Cloud is asked to keep nothing, and the room is deleted.
The same happens if Django cannot be reached or `INBOUND_CONTEXT_URL` is missing.
Every real job enters participant-aware routing; missing configuration cannot
send a SIP caller to a generic assistant. A draft or paused employee is
never put on a call, and no business conversation starts without an assigned
employee. Django records one of our numbers that rang with nobody ready as a
missed caller, so someone calls back.

### Several business lines, one secure trunk

Every business line can have its own campus and employee. One LiveKit
inbound trunk accepts all of them:

- the numbers in `TELEPHONY_INBOUND_NUMBER` (comma-separated), and
- every number routed to an employee on the Numbers & channels page.

`manage.py telephony plan` lists them, masked. Dashboard routes contribute
numbers only when routing is enabled and an employee is assigned. Explicit
environment numbers are also included. Editing a dashboard route does not
change an existing LiveKit trunk: review and apply its updated plan separately.
The worker checks current routing in Django for every SIP call, including calls
to a number still present on an older trunk. The dispatch rule is bound to that
trunk (never a wildcard), gives
each caller a new room (callee rule with a random suffix), and dispatches
the agent explicitly. Each call's room is unique, so callers never share one.

The plan stays blocked, and `telephony apply` refuses, until all of these
are confirmed in `TELEPHONY_CONFIRMED`: the account, the regional endpoint,
SRTP, and `livekit_allowed_addresses_enforced`. Add the last one only after
LiveKit confirms enforcement for the project, then test rejection against the
configured trunk before opening public routing. Simulated end to end in
`tests/django/test_inbound_simulation.py` and
`tests/voice/test_inbound_fallback.py`.

## Lifecycle of one call

1. LiveKit's dispatch rule starts a job when the call arrives.
2. The worker connects and waits for the caller (a SIP participant).
3. It posts the dialled number, the caller's number and the room to Django.
4. Django normalises both, finds the route, and recognises a known contact by
   the keyed hash of the caller's number. It creates the call record with the
   published version's snapshot, the campus, the contact and their open lead,
   and returns the employee's profile, with that campus's knowledge.
5. The worker answers with the employee's voice and greeting, and reports
   "answered".
6. When the caller hangs up or the session closes, the worker reports "ended" with talk time, latency and, if the
   organisation keeps transcripts, the transcript. It then deletes the
   room, which releases the phone line.
7. Django stores the transcript (if allowed). `analyse_calls` or "Analyse now"
   writes the summary and carries out the employee's permitted actions.

A caller on the do-not-call list is still answered: they called us. If they
ask not to be called again, the analysis adds their number to the list.

The caller's number travels only in the signed request to Django. It is
stored encrypted on the call record. Authorised staff can see the full number
and turn an unknown caller into a lead. Logs and audit entries remain masked.

The planned LiveKit inbound trunk has a configurable maximum call duration
(600 seconds by default); the worker does not add a separate duration timer.

## Opening hours, after-hours calls and missed callers

Each campus has weekly opening hours and holidays (Settings → Locations & team → the
campus). A campus with no hours counts as always open, except on holidays.

- **After hours:** the call is still answered by the routed employee. Its
  instructions get a line saying the campus is closed and when it next opens,
  plus the campus's own closed message. The employee takes a message: name,
  what they need, and a good time to call back. The call is recorded as a
  missed caller ("Called after hours").
- **Not routed:** if one of our numbers rings and no ready employee can take
  it, Django records a failed inbound call and a missed caller ("No employee
  was ready to answer"). Calls to numbers we do not know are ignored.
- **Missed callers** are listed on Incoming calls, by campus, with what the
  caller said when the call was analysed. Staff call them back from their own
  phone and press "Called back". Dialling stored contacts from the dashboard
  depends on `CONTACT_DIALING_ENABLED` and the contact's calling permission.
  Each missed caller also raises one
  notification for the campus.

## Transfer and escalation

**Built: escalation.** When the call summary says the caller asked for a
person (`human_requested`), or staff run an "Escalate to a person" action, the
system adds a call-back task for the campus's escalation contact (set on the
campus page). It also notifies them. If no contact is set, everyone in the
campus is notified. The action's result says plainly that a live transfer was
not made.

**Designed, not built: live transfer.** A caller who asks for a person should
be transferred to the number in the route's "Transfer number" field. This is
not built yet because it needs:

1. A worker tool, `transfer_to_person`, offered only when the route has a
   transfer number. It is a change to the agent's tools, so per the project
   rules it starts with a simulation scenario.
2. LiveKit SIP transfer (`TransferSIPParticipant`) on the trunk, which needs
   the trunk to be live and the provider to allow call transfer (REFER).
3. A rule for when nobody answers the transfer: take a message, which the
   analysis turns into a follow-up task.
4. A call record status for "transferred", and the transfer target recorded
   in the action log.

Until then the employee says the team will call back, and "Create a follow-up
task" puts that on the lead.
