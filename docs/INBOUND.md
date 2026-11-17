# Inbound calls: routing, lifecycle and transfer

How an incoming phone call reaches the right published AI employee, what is
built, and what stays switched off.

## What stays off

The inbound SIP trunk is not activated. `manage.py telephony plan` lists the
blockers: the provider (Vobiz) and LiveKit must authenticate the trunk, and the
regional endpoint must be confirmed, before `telephony apply` will create it.
Everything below is provider-independent and works the moment the trunk does,
with no further code change.

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
provider's limits apply), and opening hours with an out-of-hours message.

## Transfer and escalation: designed, not built

A caller who asks for a person should be transferred to the number in the
route's "Transfer number" field. Not built yet, because it needs:

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
