# Phase 2 implementation: inbound calls

Updated 14 November 2026.

**Status: the inbound integration is built and tested against a fake LiveKit. It has
not carried a real call.** No number has been bought, no trunk or dispatch rule exists
in LiveKit, and nothing is deployed. Going live waits on the items in
[VOBIZ_SETUP.md](VOBIZ_SETUP.md), which need the owner's accounts.

## What exists

| Area | Files | What it does |
| :--- | :--- | :--- |
| Settings | `apps/telephony/conf.py` | Typed, validated settings from the environment |
| Providers | `apps/telephony/providers.py` | Vobiz and Plivo profiles; the confirmations a plan depends on |
| Plan | `apps/telephony/planning.py` | The trunk and dispatch rule to create, and every blocker |
| Read | `apps/telephony/livekit_gateway.py` | Lists LiveKit trunks and rules; drops the trunk password |
| Compare | `apps/telephony/reconcile.py` | Missing, matching, duplicated, drifted or competing configuration |
| Change | `apps/telephony/provisioning.py` | Creates and deletes the trunk and rule, with rollback |
| Command | `manage.py telephony …` | `plan`, `status`, `apply`, `teardown` |
| Worker | `voice_agent/telephony.py` | Recognises phone callers; logs joins and hang-ups with masked numbers |
| Hang-up | `voice_agent/call_end.py` | Optional end-call tool, off unless `AGENT_CAN_END_CALL=true` |

The Voice Studio (`apps/core`) is unchanged. In the worker, models, greeting, turn
settings and the default prompt are unchanged; the default prompt is byte-identical.

## Commands

```powershell
.\.venv\Scripts\python.exe manage.py telephony plan                 # offline
.\.venv\Scripts\python.exe manage.py telephony status               # read-only
.\.venv\Scripts\python.exe manage.py telephony apply --dry-run      # read-only
.\.venv\Scripts\python.exe manage.py telephony apply --confirm-number +91XXXXXXXXXX --yes-change-livekit
.\.venv\Scripts\python.exe manage.py telephony teardown --dry-run   # read-only
.\.venv\Scripts\python.exe manage.py telephony teardown --confirm-trunk-id ST_xxx --yes-delete-from-livekit
```

| Command | Contacts LiveKit | Changes LiveKit |
| :--- | :--- | :--- |
| `plan` | No | No |
| `status`, `apply --dry-run`, `teardown --dry-run` | Lists only | No |
| `apply` | Yes | Creates what is missing |
| `teardown` | Yes | Deletes this plan's rule, then its trunk |

None of them contacts the SIP provider. The provider side is set up in its console.

### Safeguards in `apply`

1. Refuses while the plan has any blocker.
2. Needs the full number typed in `--confirm-number` and the `--yes-change-livekit` flag.
3. Refuses if LiveKit already holds duplicate trunks, a trunk with no authentication, a
   catch-all dispatch rule, or a trunk or rule that differs from the plan. It never
   updates or overwrites.
4. Running it twice creates nothing the second time.
5. If the dispatch rule cannot be created, the trunk created in that run is deleted
   again. A trunk that existed before is never deleted by `apply`.
6. After creating, it re-reads LiveKit. A trunk that comes back without authentication
   is removed at once.

`teardown` deletes only the trunk and rule this plan names, rule first so routing stops
before anything else, and needs the exact trunk ID. It works on a blocked plan, so a
setup can always be removed.

## What gets created

- **Inbound trunk** `telugu-inbound-<provider>`: one number, authentication, media
  encryption, 25 s ringing timeout, 600 s maximum call.
- **Dispatch rule** bound to that trunk only: one room per call named
  `call-<our number>_<random>`, caller number hidden from the worker, existing
  `telugu-agent` dispatched.
- **Provider destination** `sip:<subdomain>.india.sip.livekit.cloud;transport=tls`.

## Security

| Rule | Enforcement |
| :--- | :--- |
| Number matching is not authentication | A plan without credentials or an enforced allow-list is blocked. No setting overrides it |
| Signalling encrypted | Only `tls` passes |
| Media encrypted | `require` by default, blocked until the provider confirms SRTP. `allow` is an explicit choice with a warning. `off` is blocked |
| Secrets stay on the server | The trunk password comes from `.env.local`, is excluded from `repr`, plans, status and errors |
| Caller privacy | Numbers are masked in all Django output and worker logs; room names never contain the caller's number |

Vobiz cannot authenticate with a username and password on inbound calls (its
origination URI has no such fields), so the Vobiz path uses an address allow-list.
LiveKit saves that list but **ignores it until LiveKit support enables enforcement for
the project**. That is the main open item; see VOBIZ_SETUP.md.

## Hang-up

- **Caller hangs up:** the session already closes by itself. The worker now logs it
  with the disconnect reason.
- **Agent hangs up:** with `AGENT_CAN_END_CALL=true` the model can call `end_call`. The
  agent says one goodbye, the session closes and the room is deleted, which releases
  the phone line. Off by default.
- **Backstop:** the trunk's 600 s maximum call duration.

## Verification

| Check | Result |
| :--- | :--- |
| Unit and mocked tests | **189 passed.** 92 from Phases 1 and 1.5, 84 telephony, 8 call termination, 5 worker telephony |
| Ruff, `manage.py check` | Clean |
| Existing five simulations, end-call flag off | **5 of 5 passed** on the changed worker (14 Nov) |
| Call-termination scenarios, flag on | **6 of 6 passed**, twice. In the second run `end_call` fired exactly once in each of the four conversations that end |
| `status`, `apply --dry-run`, `teardown --dry-run` on the real project | Read-only; 0 trunks, 0 rules; nothing created |
| Real `apply` | **Not run.** The plan is blocked, as it should be |
| Real phone call, phone latency, interruption by phone | **Not done.** Needs a number |
| Browser checks | Not re-run; `apps/core` is unchanged |

Found by the scenarios and fixed: in the first run the model called `end_call`
repeatedly before its goodbye. The tool's reply now says the call is ending and not to
call a tool again.

Still observed, not fixed: with the flag on, the agent sometimes asks "anything else?"
after a thanks instead of ending. It errs toward staying on the line, which is the safer
side.

## Not yet proven

- That `<subdomain>.india.sip.livekit.cloud` works on the Build plan.
- That LiveKit will enable allow-list enforcement on the Build plan.
- That Vobiz delivers inbound calls over TLS with SRTP to LiveKit.
- That an individual Vobiz account can buy an ordinary inbound number (KYC is
  documented as Aadhaar and PAN; number eligibility is not spelled out).
- Telugu recognition on 8 kHz phone audio, and phone latency.
- Whether the greeting is clipped at the start of a phone call. The agent greets as
  soon as it connects; if the first word is lost, the fix is to wait for the caller's
  audio to be subscribed.
