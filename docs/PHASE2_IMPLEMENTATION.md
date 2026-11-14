# Phase 2 offline foundation: implementation and verification

14 November 2026. Scope approved by the owner: the no-purchase, offline part of Phase 2.

**Result: offline foundation complete. Live telephony is blocked on four confirmations
and has not been started.** No number was bought, no call was placed, no trunk or
dispatch rule was created, nothing was deployed, and nothing was committed.

## What exists now

A Django app, `apps/telephony`, that plans an inbound setup and inspects LiveKit
without being able to change it.

| File | Purpose |
| :--- | :--- |
| `conf.py` | Typed settings from the environment; malformed values are rejected |
| `providers.py` | Plivo and Vobiz profiles, and the list of confirmations a plan can depend on |
| `planning.py` | Builds the plan and its blockers; builds the trunk and dispatch requests for a future `apply`, and refuses to build them from a blocked plan |
| `livekit_gateway.py` | The only code that contacts LiveKit. It lists trunks and dispatch rules and drops the trunk password from what it returns |
| `reconcile.py` | Compares the plan with LiveKit: create what is missing, keep what matches, stop for a person on duplicates, drift or competing rules |
| `masking.py` | Phone-number masking for all output |
| `management/commands/telephony.py` | `plan`, `status`, `apply`, `teardown` |

Unchanged: `voice_agent/`, `apps/core/` (the Voice Studio), `scenarios.yaml`, all models
and prompts. `config/settings.py` gained one line registering the app.

## Commands

```powershell
.\.venv\Scripts\python.exe manage.py telephony plan          # offline, no network
.\.venv\Scripts\python.exe manage.py telephony plan --json
.\.venv\Scripts\python.exe manage.py telephony status        # lists LiveKit config, read-only
```

| Command | Network | Changes anything |
| :--- | :--- | :--- |
| `plan` | None | No |
| `status` | Two list calls to LiveKit | No |
| `apply` | None in this build | No. Checks its confirmations, then stops with "not included in this build" |
| `teardown` | None in this build | No. Same |

### Designed confirmations for the future `apply` and `teardown`

`apply` will run only when all of these hold, in this order:

1. The plan has no blockers.
2. `--confirm-number` equals the full inbound number, typed by the operator.
3. `--yes-change-livekit` is passed.
4. `status` finds no duplicates, drift, open trunks or wildcard rules.

It will then create only what is missing, bind the dispatch rule to the new trunk's ID,
and never update or delete an existing resource. `teardown` will need
`--confirm-trunk-id` and `--yes-delete-from-livekit`. Checks 1 to 3 and the `teardown`
flags are implemented and tested today; the step after them is a refusal.

## What the plan produces

With every value set and confirmed (dummy values, run on 14 November):

```text
Provider:               Plivo (India region)
Inbound number:         +91********23
LiveKit SIP endpoint:   abc123def45.india.sip.livekit.cloud
Provider sends calls to: sip:abc123def45.india.sip.livekit.cloud;transport=tls
Inbound trunk:          telugu-inbound-plivo
  Authentication:       username and password (lkinbound)
  Media encryption:     require
  Ringing timeout:      25 s
  Max call duration:    600 s
Dispatch rule:          telugu-inbound-plivo-dispatch
  Rooms:                call-<our number>_<random>, one per call
  Agent:                telugu-agent
  Caller number:        hidden from the worker
```

Design choices:

- **Existing agent reused.** The dispatch rule names `telugu-agent`, as the Voice Studio
  token already does. No second agent.
- **One room per call, no caller number in the name.** A "callee" rule with a random
  suffix names rooms after our own number. LiveKit's usual "individual" rule would put
  the caller's number in the room name and so into logs.
- **Rule bound to its trunk.** A rule without trunk IDs matches every trunk; the builder
  refuses to create one.
- **India endpoint by default** for Plivo and Vobiz
  (`<subdomain>.india.sip.livekit.cloud`). Choosing `global` for either blocks the plan.

## Security position

| Requirement | How it is enforced |
| :--- | :--- |
| Number matching is not authentication | A plan with neither credentials nor an enforced allow-list is blocked (`trunk_credentials_missing`). There is no setting that overrides this |
| Digest credentials must be proven | `provider_inbound_digest_auth` blocks until confirmed |
| Allow-list must actually be enforced | LiveKit saves `allowed_addresses` but ignores it until support enables it for the project. `livekit_allowed_addresses_enforced` blocks until confirmed. Ranges as wide as `0.0.0.0/0` are rejected outright |
| Signalling encrypted | Only `tls` passes; `tcp` blocks the plan |
| Media encrypted | Default `require`, which blocks until `provider_inbound_srtp` is confirmed. `allow` passes with a warning. `off` blocks |
| Secrets stay on the server | The trunk password is read from the environment, excluded from `repr`, and absent from plan, JSON, status and error output. Tests check each |
| No insecure trunk can be provisioned | No create, update or delete call exists in the app. A test scans the source for them |

### What was found about provider authentication

| Provider | Finding | Status |
| :--- | :--- | :--- |
| Plivo | Its origination URI object has `authentication_needed`, `username` and `password`, described as "Whether Plivo should authenticate when sending calls". The mechanism is not named. Neither Plivo's LiveKit guide nor LiveKit's Plivo guide configures any inbound authentication | Documented, untested. Blocker |
| Vobiz | Trunks support digest credentials and IP lists, but nothing says credentials are sent on inbound calls to an external address. Its own example opens the LiveKit trunk to `0.0.0.0/0` | Undocumented. Blocker |
| LiveKit allow-list | "Must be enabled for your project… Contact LiveKit support… Until it's enabled, the list is saved but not enforced". No plan is stated | Needs a support request. Blocker |

Sources: <https://www.plivo.com/docs/sip-trunking/api/origination-uris>,
<https://docs.livekit.io/telephony/start/providers/plivo/>,
<https://docs.livekit.io/telephony/accepting-calls/inbound-trunk/>.

## Verification

| Check | Result |
| :--- | :--- |
| Full test suite | **158 passed, 1 skipped**. The skip is the call-termination specification, by design |
| Existing tests | The 92 from Phases 1 and 1.5 all pass, unmodified |
| New telephony tests | 66, all mocked: settings validation, every blocker, request contents, room isolation, read-only gateway, duplicates, drift, error handling, command refusals |
| Ruff lint and format | Clean |
| `manage.py check` | No issues |
| Process boundary test | Passes: Django imports `livekit.api` only, never the voice stack |
| `plan` makes no network call | Tested with the LiveKit client replaced by one that fails on any use |
| `status` only lists | Tested with a fake that fails on any call other than the two list calls |
| `status` against the real project | Run once, read-only: 0 inbound trunks, 0 dispatch rules. Nothing was created |
| Voice worker, Voice Studio, `scenarios.yaml` | No diff |
| Five simulations | Not re-run: agent code, prompt and scenarios are unchanged |
| Browser checks | Not re-run: no file under `apps/core` changed |

The `ruff.exe` launcher had disappeared again and was restored from the uv cache, as
described in the README.

## Complete, and waiting

Complete:

- Provider-neutral telephony app with typed, validated settings.
- Offline `plan` and read-only `status`.
- Secure trunk and dispatch planning with the India endpoint.
- Masked numbers and isolated room names.
- Confirmation gates for `apply` and `teardown`.
- Call-termination design, tests and scenarios (see `CALL_TERMINATION_DESIGN.md`).
- `.gitignore` hardening.

Waiting on LiveKit:

| Token for `TELEPHONY_CONFIRMED` | Question to put in writing |
| :--- | :--- |
| `livekit_regional_sip_endpoint` | Does `<subdomain>.india.sip.livekit.cloud` accept inbound SIP on a Build-plan project? |
| `livekit_allowed_addresses_enforced` | Only if the provider cannot do digest: can `allowed_addresses` be enabled for this project, and on which plan? |

Waiting on the provider:

| Token | Question |
| :--- | :--- |
| `provider_account_ready` | Is this entity eligible, is KYC approved, and is the number ours? |
| `provider_inbound_digest_auth` | Do you answer a SIP digest challenge (407) from the destination on inbound calls, using the origination URI's username and password? |
| `provider_inbound_srtp` | Is SRTP offered on inbound calls to an external SIP address over TLS? |

Waiting on the owner:

- Approval of the checkpoint in `PHASE2_READINESS.md` section 2 (commit, tag, backup).
- Review of `CALL_TERMINATION_DESIGN.md`.
- Approval, later, to add real provisioning to `apply`.

Not built in this round, by choice: a telephony page in the Voice Studio, any change to
the voice worker for SIP callers, and anything from Phase 3.
