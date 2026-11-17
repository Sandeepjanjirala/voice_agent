# Inbound calls: routing, lifecycle and transfer

How an incoming phone call reaches the right published AI employee, what is
built, and what stays switched off.

## What stays off

The inbound SIP trunk is not activated. `manage.py telephony plan` lists the
blockers. `telephony apply` will not create the trunk until LiveKit enforces the
address allow-list and the Vobiz account owns a number.
The routing backend is tested with mocks. Worker fallback and live security
verification still need the gates in [PILOT_READINESS.md](PILOT_READINESS.md);
activating a trunk alone is not end-to-end acceptance.

## What LiveKit confirmed (16 November 2026)

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
| Username and password on inbound? | Believed supported. | Does not help with Vobiz: its origination URI cannot send credentials, so the allow-list is the only authentication. |

**What still blocks inbound calls:**

1. LiveKit has to say that enforcement is on for `p_44waokrv9lv`.
2. A test call from an address that is not on the list has to be rejected. The
   check is in [VOBIZ_SETUP.md](VOBIZ_SETUP.md), step 5.
3. Only then does the owner add `livekit_allowed_addresses_enforced` to
   `TELEPHONY_CONFIRMED`.

Until all three are done, the trunk stays unprovisioned. Without enforcement
LiveKit saves the list but accepts INVITEs from anyone, so a public inbound
trunk would let anyone on the internet start a paid AI call.

Still to come from Vobiz: a number, its signalling addresses (three are
documented), and SRTP on inbound calls (`provider_inbound_srtp`).

## Routing

| Piece | Where |
| :--- | :--- |
| Business lines, their campus and their employee | Numbers & channels page, `apps/calling/models.PhoneNumber` |
| Which employee answers, now | `apps/calling/inbound.route_for`: the number is routed, its employee is published and switched on, and both belong to the same organisation |
| The worker's question | `POST /api/telephony/inbound-context/`, signed with `CALL_EVENTS_SECRET` |
| The worker's side | `voice_agent/inbound.py`, used when `INBOUND_CONTEXT_URL` is set |

A number that is not routed answers 404, and the worker then answers with its
own default profile, as it did before. A draft or paused employee is never
put on a call.

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
   organisation keeps transcripts, the masked transcript. It then deletes the
   room, which releases the phone line.
7. Django stores the transcript (if allowed). `analyse_calls` or "Analyse now"
   writes the summary and carries out the employee's permitted actions.

A caller on the do-not-call list is still answered: they called us. If they
ask not to be called again, the analysis adds their number to the list.

The caller's number travels only in the signed request to Django. It is
stored encrypted on the call record so that staff can turn an unknown caller
into a lead without seeing the number, and is never logged or shown.

Not yet built: a time limit on inbound calls of our own (LiveKit and the
provider's limits apply).

## Opening hours, after-hours calls and missed callers

Each campus has weekly opening hours and holidays (Campuses & staff → the
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
  stays off (`CONTACT_DIALING_ENABLED`). Each missed caller also raises one
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
