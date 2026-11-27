# Vobiz: set up the first incoming call

**Current position, 27 November 2026:** this account has a trial number only.
Vobiz trial numbers cannot receive incoming calls. A verified account and an
active, fully provisioned number are required. No paid number has been purchased
as part of this setup. [Vobiz trial-number requirements](https://www.vobiz.ai/docs/faq/trial-inbound)

The steps below separate preparation from the owner's purchases, LiveKit changes
and first real call. The commands are instructions to review and run when ready;
writing this guide does not run them.

The call path is:

```text
Caller -> purchased Vobiz number -> Vobiz inbound trunk -> LiveKit Cloud
                                                        -> voice worker on this PC
                                                           -> Django on this PC
```

For this local setup, Django runs at `http://127.0.0.1:8002/`. The worker connects
to LiveKit Cloud and calls Django locally. Django does not need a public URL or
ngrok while both processes run on this PC. Keep both processes running to answer
calls. A later cloud deployment needs a backend URL reachable from that worker.

## 1. Before paying: confirm LiveKit filtering and complete Vobiz KYC

### LiveKit

1. Sign in to [LiveKit Cloud](https://cloud.livekit.io/) and select
   **telugu-ai-calling-agent**, project **`p_44waokrv9lv`**. This project's URL
   and API key have been matched to the workspace's configuration.
2. Open **Settings -> Project** and verify its **SIP URI**. The verified SIP
   subdomain is `44waokrv9lv`, already saved in `.env.local`. If the workspace
   moves to another project, repeat this check using that project's URL, API
   credentials and SIP URI.
3. The India endpoint is `44waokrv9lv.india.sip.livekit.cloud`. The regional
   SIP endpoint is separate from protocol-based region pinning for realtime
   SDKs. [LiveKit telephony regions](https://docs.livekit.io/telephony/features/region-pinning/)
4. Through LiveKit support or the community, request **enforcement** of
   `allowed_addresses` for this exact project. Obtain written confirmation that
   it is enabled before paying for the number.

Suggested request:

> I am connecting Vobiz to the India SIP endpoint for project `p_44waokrv9lv`.
> Please enable inbound trunk `allowed_addresses` enforcement for this project
> and confirm when it is active. Please also give me a supported way to verify
> rejection from an unlisted source after the trunk is configured.

LiveKit explicitly states that an allowlist can be saved without being enforced.
A list visible in the console is therefore not proof of protection.
[LiveKit inbound authentication](https://docs.livekit.io/telephony/accepting-calls/inbound-trunk/)

### Vobiz

1. Sign in to the [Vobiz Console](https://console.vobiz.ai/).
2. Complete **KYC**. Individuals use Aadhaar and PAN; registered companies use
   PAN and GST. [Vobiz India KYC](https://www.vobiz.ai/docs/compliance/india/kyc)
3. Ask console support or `support@vobiz.ai` to confirm the following for your
   account before adding money:
   - Which dedicated Indian number can receive calls through SIP, its setup
     fee, monthly rental, inbound per-minute rate, minimum recharge and taxes.
   - Whether the account includes capacity for one incoming call at a time or
     needs a separate channel subscription.
   - TLS to the LiveKit India endpoint and SRTP on calls delivered to it.
   - The current origination source IP addresses and the dialled-number format
     sent to LiveKit; the application expects the purchased number in E.164.

Vobiz documents TLS and SRTP as separate settings. Its public origination
configuration does not document sending digest credentials to LiveKit, so this
setup uses LiveKit's enforced IP allowlist.
[Vobiz trunk settings](https://www.vobiz.ai/docs/trunks/trunk-object)

## 2. Owner: purchase and activate one incoming number

After the checks above, review the total cost, add the required payment method
and purchase one dedicated number with incoming calling enabled. A trial caller
ID or an outbound trunk alone does not enable incoming calls.
[Vobiz upgrade steps](https://www.vobiz.ai/docs/faq/trial-inbound)

Save the purchased number in full E.164 format, for example `+91XXXXXXXXXX`.
Keep the number unlinked from the LiveKit destination until the routing and
trunk steps below are complete. Retain the provider's activation confirmation.

## 3. Choose who answers in Vaani

Open [the local dashboard](http://127.0.0.1:8002/) and sign in.

1. In **AI employees**, choose **Answers incoming calls**, or open the
   employee that should answer this line.
2. Review its business facts, language, voice, greeting, questions and permitted
   actions. Save the draft, **Publish**, and ensure the employee is switched on.
   Saving a draft alone does not change the version callers hear.
3. Open **Numbers & channels** and add the purchased number. Choose its
   **Location** and **AI employee who answers**.
4. Enable **Answer calls to this number with the employee** and save.
5. Check **Incoming calls** for routing problems. A missing assignment, draft
   employee or paused employee cannot answer a business call.

The number, location and employee must belong to the same organisation. Put
business-specific instructions and knowledge in that organisation and employee.
The optional transfer-number field does not enable a live transfer; see
[inbound routing and escalation](INBOUND.md).

## 4. Complete `.env.local`

Edit the existing file privately. Keep credentials out of chat, screenshots and
version control. These non-secret values have already been saved locally:

```ini
TELEPHONY_PROVIDER=vobiz
LIVEKIT_SIP_SUBDOMAIN=44waokrv9lv
LIVEKIT_SIP_REGION=india
SIP_INBOUND_AUTH_MODE=ip_allowlist
SIP_INBOUND_TRANSPORT=tls
SIP_MEDIA_ENCRYPTION=require
CALL_EVENTS_URL=http://127.0.0.1:8002/api/telephony/call-events/
INBOUND_CONTEXT_URL=http://127.0.0.1:8002/api/telephony/inbound-context/
CALL_PROFILE_URL=http://127.0.0.1:8002/api/telephony/call-profile/
CALL_RECORDING_URL=http://127.0.0.1:8002/api/telephony/call-recording/
```

The number, source IPs and new account confirmations remain unset. Fill these
fields only with the purchased number and confirmed details:

```ini
TELEPHONY_INBOUND_NUMBER=+91XXXXXXXXXX
SIP_INBOUND_ALLOWED_ADDRESSES=<confirmed origination IPs, comma-separated>
```

`LIVEKIT_SIP_SUBDOMAIN` contains only the subdomain: no `sip:`, region, hostname
suffix or port. Keep the existing `LIVEKIT_URL`, API key, API secret,
`CALL_EVENTS_SECRET` and `AGENT_NAME`. Django and the worker must share the same
`CALL_EVENTS_SECRET` of at least 32 characters.

As of 27 November, Vobiz lists the following origination sources:

```text
13.233.44.61,3.111.255.163,3.111.128.110
```

Confirm that list with Vobiz before entering it. Its documentation distinguishes
these three origination sources from its larger signalling and media IP lists;
addresses can change. Do not use `0.0.0.0/0`.
[Vobiz source addresses](https://www.vobiz.ai/docs/concepts/ip-whitelisting)

Append these items to the existing comma-separated `TELEPHONY_CONFIRMED` value
only when each fact has been confirmed:

| Entry | Evidence needed |
| --- | --- |
| `provider_account_ready` | Verified, funded Vobiz account and active incoming number. |
| `livekit_regional_sip_endpoint` | Correct current-project India SIP endpoint. |
| `livekit_allowed_addresses_enforced` | LiveKit's written confirmation that enforcement is enabled for this project. |
| `provider_inbound_srtp` | Vobiz confirmation of SRTP for delivery to the configured destination. |

Preserve all existing entries, including `provider_outbound_ready`; do not replace
the line with an inbound-only list. These entries record evidence, not requests
to enable provider features. Keep `SIP_MEDIA_ENCRYPTION=require`; if the provider
cannot supply SRTP, resolve that limitation before proceeding.

## 5. Review and provision the LiveKit trunk

In PowerShell, from the repository:

```powershell
Set-Location -LiteralPath 'C:\Users\Sandeep.J\Documents\agent'
.\.venv\Scripts\python.exe manage.py telephony plan
.\.venv\Scripts\python.exe manage.py telephony apply --dry-run
```

`plan` is local and lists blockers. `apply --dry-run` reads LiveKit and previews
changes; it also refuses while the plan is blocked. Check the numbers, India
endpoint, IP allowlist, required encryption and worker name. Resolve every
blocker before proceeding.

**After the owner approves creating the LiveKit configuration**, replace the
placeholder with the purchased number and run:

```powershell
.\.venv\Scripts\python.exe manage.py telephony apply --confirm-number +91XXXXXXXXXX --yes-change-livekit
.\.venv\Scripts\python.exe manage.py telephony status
```

This creates the missing LiveKit inbound trunk and its scoped dispatch rule. It
does not buy a number, configure Vobiz or place a call. The rule uses the configured
`AGENT_NAME` and gives calls separate rooms. Do not create a second manual trunk
or a wildcard dispatch rule for the same number.

`status` checks stored configuration. Even a displayed `authenticated` label
does not prove server-side IP enforcement. Complete LiveKit's supported negative
test against a configured trunk and compare it with an accepted provider call.
A rejection before any trunk exists proves nothing about filtering. If this must
be verified before an agent can answer, arrange a separate staging trunk without
agent dispatch with LiveKit support; the normal `apply` command creates both.

## 6. Owner: connect the Vobiz inbound trunk

Use the **inbound** section in Vobiz. The outbound trunk and its username/password
serve calls placed through Vobiz; incoming calls need a destination URI and the
purchased number linked to the inbound trunk.

1. Open **SIP -> Inbound -> Origination URIs** and create a destination:

   | Field | Value |
   | --- | --- |
   | URI | `44waokrv9lv.india.sip.livekit.cloud:5061` |
   | Transport | `TLS` |
   | Active | Enabled |

   Take the host from `telephony plan`'s **Provider sends calls to** value. For
   this console field, remove the `sip:` prefix and `;transport=tls` suffix and
   append `:5061`. Set TLS separately. The console format differs from Vobiz's
   API SIP URI format. [Vobiz LiveKit integration](https://www.vobiz.ai/docs/integrations/livekit)
2. Open **SIP -> Inbound -> Trunks** and create an inbound trunk. Choose the
   destination as its **Primary URI**, enable **Secure / SRTP**, and enable the
   trunk. Keep recording and transcription consistent with the organisation's
   policy; leave them off for an unrecorded first test.
3. Ensure the **origination URI itself** uses TLS. When a primary URI is attached,
   its transport controls signalling; TLS only on the trunk is insufficient.
   [Vobiz TLS and SRTP configuration](https://www.vobiz.ai/docs/integrations/openai-realtime#step-2-create-the-inbound-trunk)
4. Select **Link Numbers**, choose the purchased DID, and confirm it appears
   under **Linked Numbers**. This connects incoming phone calls to LiveKit.

TLS protects signalling; SRTP protects audio. Selecting port `5061` alone does
not turn on TLS. LiveKit requires media encryption on its trunk as well.
[LiveKit secure trunking](https://docs.livekit.io/telephony/features/secure-trunking/)

## 7. Restart both processes and make the approved first call

After configuration changes, stop the existing Django and worker processes with
`Ctrl+C` in their respective terminals. Start Django in one PowerShell terminal:

```powershell
Set-Location -LiteralPath 'C:\Users\Sandeep.J\Documents\agent'
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8002
```

Start the separate voice worker in another terminal:

```powershell
Set-Location -LiteralPath 'C:\Users\Sandeep.J\Documents\agent'
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m voice_agent.agent dev
```

Wait for the worker to connect. Recheck **Incoming calls** in the dashboard.
With the owner's approval, call the **purchased Vobiz number** from your mobile:

1. Hear the intended employee's complete greeting.
2. Ask an English or Telugu question, then a mixed Telugu-English question.
3. Check that both sides hear audio and that interrupting a reply works.
4. Hang up and verify that the call ends and appears under **Incoming calls**
   with the correct employee and location.
5. Check that saved transcripts and recordings follow the organisation's policy.

For an unrecorded test, also keep worker transcript logging off with
`VOICE_LOG_TRANSCRIPTS=false`; restart the worker after changing it.

If the trial number is dialled, no incoming connection is expected. If the paid
number never reaches LiveKit, check Vobiz's linked number, active trunk, primary
URI and call logs. If LiveKit receives it but no employee answers, check the
worker connection, dispatch name, published route and local Django availability.
An unrouted call or unreachable Django backend must end without starting a
business conversation. See [INBOUND.md](INBOUND.md) for the lifecycle and
[PILOT_READINESS.md](PILOT_READINESS.md) for the remaining acceptance checks.
