# Vobiz setup for the first inbound call

For an individual in India without GST. Written 14 November 2026 from Vobiz's and
LiveKit's published pages; nothing here has been tried on a real account. Steps marked
**You** need your own login, documents or payment.

## What is known

| Question | Answer | Evidence |
| :--- | :--- | :--- |
| Can an individual pass KYC? | Yes: "Individuals: Aadhaar card + PAN card". GST is asked only of organisations | Vobiz FAQ and KYC page |
| Can an individual then buy an ordinary inbound number? | Not stated outright. The KYC page says it is "to start renting Indian phone numbers"; only 140, 160 and 92-series need extra approval | Vobiz FAQ. **Ask before paying** |
| Inbound on a trial? | No. Trial numbers are outbound only | Vobiz FAQ |
| Prices | Not published for India. Buying a number charges a setup fee plus the first month at once. "All payments made to Vobiz are final" | Vobiz docs and terms |
| Username and password on inbound calls? | No. The destination ("origination URI") has no credential fields | Vobiz object reference |
| Where do its calls come from? | 13.233.44.61, 3.111.255.163, 3.111.128.110 | Vobiz IP page. Confirm with support |
| Encryption | Trunks have a TLS transport option (console) and a "secure" switch for SRTP | Vobiz docs. Untested with LiveKit |

## The one real obstacle

Because Vobiz cannot send credentials, the only way to stop strangers reaching the
trunk is LiveKit's address allow-list. LiveKit's docs say that list "must be enabled for
your project… Contact LiveKit support… Until it's enabled, the list is saved but not
enforced, and the trunk accepts calls from any address."

The free Build plan has community support only. If LiveKit will not enable this on
Build, the choices are the $50/month Ship plan, or a provider that sends credentials
(Plivo documents it but needs business papers; Exotel reports it and accepts
individuals, at a higher entry price). **Do step 1 first and do not buy a number until
it is answered.**

## Steps

### 1. You: ask LiveKit (free): answered 16 November 2026

Asked in [community topic 2530](https://community.livekit.io/t/2530). LiveKit's answers:

- The regional endpoint works on Build, with no approval needed.
- They recommend deploying the agent to `ap-south`.
- Allow-list enforcement can be enabled on request.

Next, reply in that thread asking them to switch enforcement on for
`p_44waokrv9lv`, and wait until they say it is on. Details are in
[INBOUND.md](INBOUND.md#what-livekit-confirmed-16-november-2026).

The original question, for reference:

> Project on the Build plan, region India. I am connecting an Indian SIP provider
> (Vobiz) for inbound calls. Two questions:
> 1. Is the regional SIP endpoint `<my-subdomain>.india.sip.livekit.cloud` usable for
>    inbound trunks on the Build plan?
> 2. My provider cannot send SIP digest credentials on inbound calls, so I need
>    `allowed_addresses` on the inbound trunk to be enforced. Can you enable it for my
>    project, and is it available on Build?

### 2. You: create the Vobiz account and ask support (free)

Sign up at <https://console.vobiz.ai>, complete Aadhaar e-KYC and PAN, then ask in the
console chat or at support@vobiz.ai before adding money:

> I am an individual (Aadhaar + PAN KYC, no GST). I want one inbound number routed to
> my own SIP server (LiveKit Cloud).
> 1. Can my account buy a normal local inbound number? Which cities?
> 2. Setup fee, monthly rental, inbound per-minute rate, minimum recharge, GST?
> 3. Do I need a paid channel subscription for one inbound call at a time?
> 4. Can the origination URI use TLS, and does the trunk's "secure" option give SRTP
>    on inbound calls to an external address?
> 5. From which IP addresses do you send inbound calls to my origination URI?
> 6. Is the dialled number sent as +91XXXXXXXXXX or 0XXXXXXXXXX?
> 7. Can the destination be a regional host such as
>    `<id>.india.sip.livekit.cloud`?

If the total for a first month is above ₹1,000, stop and tell me; we compare again.

### 3. You: buy one number and point it at LiveKit

Only after steps 1 and 2 come back positive.

1. Add the smallest recharge that covers one number and about 100 minutes.
2. Buy one inbound number.
3. In **SIP Trunks**, create an origination URI with the value printed by
   `manage.py telephony plan` as "Provider sends calls to", without the `sip:` prefix
   and without `;transport=tls`. Choose **TLS** as its transport in the console.
4. Create an inbound trunk with that URI as primary, switch **secure** on, and link
   the number.
5. Leave call recording off.

### 4. You: fill in `.env.local`

```ini
TELEPHONY_PROVIDER=vobiz
TELEPHONY_INBOUND_NUMBER=+91XXXXXXXXXX
# LiveKit Cloud > Settings > Project: the project ID without "p_"
LIVEKIT_SIP_SUBDOMAIN=
SIP_INBOUND_ALLOWED_ADDRESSES=<the addresses Vobiz confirmed, comma-separated>
SIP_MEDIA_ENCRYPTION=require
# Add each item only when you hold it in writing:
TELEPHONY_CONFIRMED=provider_account_ready,livekit_regional_sip_endpoint,livekit_allowed_addresses_enforced,provider_inbound_srtp
```

If Vobiz says SRTP is not available on inbound, do not remove the entry quietly: set
`SIP_MEDIA_ENCRYPTION=allow`, drop `provider_inbound_srtp`, and we record it as a
provider limitation. Signalling stays on TLS either way.

### 5. Provision (I can run this with you)

```powershell
.\.venv\Scripts\python.exe manage.py telephony plan
.\.venv\Scripts\python.exe manage.py telephony apply --dry-run
.\.venv\Scripts\python.exe manage.py telephony apply --confirm-number +91XXXXXXXXXX --yes-change-livekit
.\.venv\Scripts\python.exe manage.py telephony status
```

### 6. First call

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m voice_agent.agent dev
```

Call the number from your own mobile. Then, in order: greeting heard in full; a Telugu
question; a mixed Telugu-English question; interrupt a reply; hang up. The worker log
shows `participant joined` with `channel: sip` and a masked number, and
`participant left` with `CLIENT_INITIATED`.

Security check: `telephony status` must show the trunk as `authenticated`. To prove the
allow-list is really enforced, LiveKit's telephony dashboard should show a rejected
attempt for any call that does not come from Vobiz's addresses; ask LiveKit how to test
this without a second provider.

For this test set `VOICE_LOG_TRANSCRIPTS=false` if you do not want what you say written
to the worker log. Latency is recorded without text:

```powershell
.\.venv\Scripts\python.exe scripts\latency_report.py
```

To undo everything on the LiveKit side:

```powershell
.\.venv\Scripts\python.exe manage.py telephony teardown --dry-run
```

## Sources

- <https://www.vobiz.ai/docs/compliance/india/kyc>
- <https://www.vobiz.ai/docs/faq.md>
- <https://www.vobiz.ai/docs/integrations/livekit>
- <https://www.vobiz.ai/docs/concepts/ip-whitelisting.md>
- <https://vobiz.ai/docs/trunks/origination-uri/origination-uri-object>
- <https://www.vobiz.ai/docs/trunks/trunk-object>
- <https://docs.livekit.io/telephony/accepting-calls/inbound-trunk/>
- <https://docs.livekit.io/telephony/features/secure-trunking/>
