# First real call: one outbound test call to your own mobile

14 November 2026. This is a test path, built so a real call can happen while secure
inbound waits on LiveKit. It dials exactly one number, once, when you run the command.
No call has been placed yet; the code has only been run against a fake LiveKit.

## How it works

1. `manage.py telephony call` (or the Phone calls page) checks the number against the
   dialing policy (`OUTBOUND_DIALING_MODE`, `OUTBOUND_ALLOWLIST_NUMBERS`; see the
   README) and asks the existing `telugu-agent` worker to place the call.
2. The worker checks the number against the same policy, dials through the stored
   LiveKit trunk and waits.
3. Only when you answer does the conversation start, with the usual Telugu greeting.
4. When either side hangs up, or the call was busy, rejected, unanswered or failed, the
   worker deletes the room, which releases the phone line. Nothing is retried.

Limits built in: only allowlisted numbers (by default), one call at a time, 30 s ringing, 5 minutes per
call, caller's number hidden from logs.

## What Vobiz needs from you

Creating an account does not by itself permit Indian calls. From Vobiz's pages:

| Requirement | What Vobiz says | Status |
| :--- | :--- | :--- |
| KYC | Required "before renting India phone numbers" and before making or receiving calls. Individuals: Aadhaar e-KYC and PAN | You |
| Caller ID | "Outbound calls must originate from a Vobiz India phone number as the caller ID" | You need one Vobiz number |
| Trial | New accounts get starter credit (a search result says ₹25, about 50 domestic minutes, and that it can pay for a number; I could not open the page that states this). Trial numbers are for outbound testing | **Ask**, see below |
| Destinations on trial | Not published: whether a trial may call any Indian mobile or only verified ones | **Ask** |
| Media anchoring | Media must stay in India, both ends Indian; otherwise the call ends with "Violates Media Anchoring" | Handled: the trunk sets `destination_country=in` so LiveKit dials from its India region. Untested |
| SIP trunk | An outbound trunk with a username-and-password credential attached; its SIP domain looks like `<id>.sip.vobiz.ai` | You create it |
| TLS and SRTP | Vobiz lists UDP and TCP signalling; the trunk has a "secure" switch for SRTP. TLS for outbound is not stated | **Ask** |

### You: send this to Vobiz support before paying anything

> I am an individual with Aadhaar + PAN KYC. I want to place test calls from LiveKit
> Cloud through a Vobiz outbound SIP trunk to my own Indian mobile.
> 1. After KYC, is my account allowed to call Indian mobile numbers? Is anything else
>    needed to enable domestic outbound calling?
> 2. Does the trial credit let me call my own mobile, and which number will be my
>    caller ID? Do I have to buy a number first, and what does it cost?
> 3. For a few test calls to my own number from an ordinary Vobiz number, do I need
>    DLT registration or a 140/160-series number?
> 4. Does the outbound trunk accept TLS signalling and SRTP media ("secure")? On which
>    host and port?
> 5. LiveKit will dial from its India region. Is that enough for media anchoring?

### You: set up in the Vobiz console

1. Complete KYC.
2. Get the caller ID number (trial number or one purchased number).
3. **SIP Trunks → create an outbound trunk.** Note its SIP domain.
4. **Create a credential** (username, and a password of 12 or more characters) and
   attach it to the trunk. Copy the password when it is shown; Vobiz does not show it
   again.
5. Switch **secure** on if Vobiz confirmed SRTP.
6. Leave recording off.

## Configure

In `.env.local` (never committed):

```ini
SIP_OUTBOUND_ADDRESS=<id>.sip.vobiz.ai
SIP_OUTBOUND_AUTH_USERNAME=<credential username>
SIP_OUTBOUND_AUTH_PASSWORD=<credential password>
SIP_OUTBOUND_CALLER_ID=+91XXXXXXXXXX        # your Vobiz number
OUTBOUND_ALLOWLIST_NUMBERS=+91XXXXXXXXXX    # your own mobile (comma-separate more)
TELEPHONY_CONFIRMED=provider_outbound_ready
```

`provider_outbound_ready` is your statement that Vobiz confirmed KYC, permission to call
Indian numbers, and that the caller ID is yours. Keep any inbound entries already on
that line.

If Vobiz says it has no TLS for outbound trunks, do not work around it silently. Set
`SIP_OUTBOUND_TRANSPORT=tcp` and add `outbound_plain_signalling_accepted` to
`TELEPHONY_CONFIRMED`; the plan then records it as an accepted limitation. If SRTP is
not available, set `SIP_OUTBOUND_MEDIA_ENCRYPTION=allow`. Authentication is never
optional.

## Run

```powershell
.\.venv\Scripts\python.exe manage.py telephony outbound-plan
.\.venv\Scripts\python.exe manage.py telephony outbound-apply --dry-run
.\.venv\Scripts\python.exe manage.py telephony outbound-apply --yes-change-livekit
```

Start the worker in a second window and leave it running. Restart it if it was already
running, so it picks up the new settings:

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m voice_agent.agent dev
```

**The one command that places a real, billed call. You run it:**

```powershell
.\.venv\Scripts\python.exe manage.py telephony call --to +91XXXXXXXXXX --yes-place-one-call
```

It prints the call status as it changes (`dialing`, `ringing`, `active`).

## What to check on the call

| Check | Look for |
| :--- | :--- |
| Phone rings | Caller ID is your Vobiz number |
| Greeting | Telugu greeting, complete from the first word, natural voice |
| Two-way audio | Ask "మీరు ఎవరు?"; it answers |
| Mixed speech | "నాకు రేపు morning ఒక appointment కావాలి" |
| Interruption | Talk over a reply; it stops |
| Hang up | End the call on your phone; the worker logs `participant left` with `CLIENT_INITIATED`, and `telephony call` reports the room closed |

Afterwards:

```powershell
.\.venv\Scripts\python.exe scripts\latency_report.py
```

Set `VOICE_LOG_TRANSCRIPTS=false` beforehand if you do not want your words in the worker
log; latency rows never contain text.

## If it does not connect

The worker log line `outbound call did not connect` carries an `outcome` and the SIP
status.

| Outcome / status | Likely cause |
| :--- | :--- |
| `busy_or_rejected` (486, 603) | You declined, or the line was busy |
| `no_answer` (408, 480) | Not answered within 30 s |
| `failed` 401 / 403 / 407 | Wrong username or password, or the credential is not attached to the trunk |
| `failed` 403 with "Violates Media Anchoring" | LiveKit did not dial from India; tell me, this is the regional routing question |
| `failed` 404 / 484 | Number format; Vobiz may want a different form of the dialled number |
| `failed` 488 / 503 right after dialling | TLS or SRTP not accepted by Vobiz; see "Configure" above |
| `refused` | The number is not allowed by the dialing policy, or the worker was not restarted after a change |

To remove the trunk from LiveKit:

```powershell
.\.venv\Scripts\python.exe manage.py telephony outbound-teardown --yes-delete-from-livekit
```

## Not covered by this test path

- The answered half of the call (session start with the callee, greeting) cannot be
  exercised without a real call, so the first call is its first run.
- Noise cancellation is not applied on this path yet.
- No dashboard, call records, scheduling, retries or multiple numbers. Those belong to
  the full Phase 3 and need consent and DLT rules settled first.

## Sources

- <https://docs.livekit.io/telephony/making-calls/outbound-calls/>
- <https://docs.livekit.io/telephony/making-calls/outbound-trunk/>
- <https://docs.livekit.io/telephony/features/region-pinning/>
- <https://github.com/vobiz-ai/LiveKit-Vobiz-Outbound>
- <https://www.vobiz.ai/docs/faq/domestic-calling-india>
- <https://www.vobiz.ai/docs/faq/trial-inbound>
- <https://www.vobiz.ai/docs/concepts/ip-whitelisting.md>
