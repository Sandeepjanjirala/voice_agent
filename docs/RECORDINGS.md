# Call recordings

Status (27 November 2026): **built and tested, off by default.** It is switched
on per organisation under Settings. Normal recording modes require the person
to agree on the call; the separate demo mode applies only to approved test calls.
Automated tests cover it end to end, including a
real recording of synthetic audio and playback in Chromium. The first real
phone call is for the owner to make; the steps are at the end of this page.

## How it works

1. **Django decides whether a call may be recorded** when it plans the call:
   - The organisation's setting is one of:
     - Never (the default)
     - Demo test calls, without a recording question: an explicitly marked
       outbound test or rehearsal **and** an approved test destination. Both
       are required. Ordinary Make calls calls and inbound calls are not
       recorded in this mode, even if their number is allowlisted.
     - Approved test calls only: calls to or from a number in
       `OUTBOUND_ALLOWLIST_NUMBERS`
     - Any call
   - The decision and its reason are kept with the call.
   - Allowed consent-mode calls carry the question to the worker. Eligible demo
     calls carry `authorization: demo`, without a recording question.
2. **For consent modes, the question is part of the opening line.** For example: "…Is it okay if
   this call is recorded so our team can review it?" It is said word for word,
   before anything is recorded. The AI employee passes the answer to a
   `recording_consent` tool:
   - Clear yes: recording starts.
   - No, an unclear answer or silence: nothing is recorded, and the call goes
     on.
   - Yes, then "please don't record": recording stops and the file is deleted
     at once.
   - Demo tests instead start recording before the greeting, without a spoken
     question or announcement. The `stop_recording` tool stops and discards
     audio if the tester asks. The agent answers recording questions truthfully.
3. **What is recorded.** The voice worker records with LiveKit's own session
   recorder (`RecorderIO`, the component behind LiveKit's Agent insights audio),
   attached at consent, or before the greeting for an eligible demo test:
   - The person is on the left channel, as their audio arrived.
   - The AI employee is on the right channel, as it was actually played to
     them, so an interrupted reply stops where the person cut it off.
   - It is the real call audio, never regenerated speech. The file is Ogg Opus
     at 32 kbit/s.
4. **The upload.** When the call ends (hang-up, `end_call`, time limit, or a
   dropped line), the worker closes the file and uploads it to Django:
   - The upload is signed with `CALL_EVENTS_SECRET` over the room, the times
     and every byte.
   - It is retried twice on network or server errors.
   - The worker's local copy is deleted either way.
5. **Storage.** Django accepts audio only under the call's saved authorization,
   unless the person declined or requested a stop. It then:
   - checks that the file is Ogg and at most 50 MB
   - encrypts it with `RECORDING_ENCRYPTION_KEYS` (Fernet)
   - stores it in a private folder, or a private S3 bucket that adds its own
     server-side encryption
6. **Playback.** On the call page (Conversations → a call → Recording):
   - Play and pause, a seek bar with times, and the total length.
   - Clicking a line of the conversation plays from there.
   - Download is offered to admins only.
   - The audio is streamed by Django through a link that works for ten
     minutes, for that person only. The browser never sees a storage URL, a
     key or a phone number.

### Status shown on the call page

| Status | Meaning |
| :--- | :--- |
| Recording | Recording started under verbal consent or approved demo authorization |
| Processing | The audio has arrived and is being stored |
| Available | Ready to play |
| Failed | Recording could not start, the upload failed, or the audio never arrived within 15 minutes of the call ending. The reason is shown |
| Not recorded | The person did not agree, recording is off for the organisation, it was not an approved test call, or nobody answered. The reason is shown |
| Deleted | Removed under the retention policy. When is shown |

Demo rows store `authorization=demo` and `authorized_at`, leaving `consented_at`
empty. Verbal-consent rows store `authorization=verbal` and the consent time.
Playback labels demo audio accurately. A late start event or upload cannot
override a request to stop and discard.

To use demo recording, an organisation admin selects the demo mode, chooses a
published AI employee and starts an approved **test call** or lead **rehearsal**:
**Leads & contacts → sample lead → Call the test number**.
The test-calls API is `POST /api/telephony/calls/` with `confirm: true`, an approved
number `slot`, and an `employee` ID. A normal Make calls action is a business call
and does not qualify. Do not call a real customer using this demo test path.

Verification on 27 November 2026: local recording/voice tests pass, including
synthetic stereo audio, the demo guard, consent metadata, and stop/discard.
The text debugger used the real model: the demo greeting omitted the question
and a stop request invoked `stop_recording`. The consent greeting retained the
question and a refusal invoked `recording_consent(agreed=false)`.
Two scenarios in `scenarios.yaml`
cover demo stop and ordinary refusal, with a final-state check; they run with
the existing merge-to-main simulation workflow. The attempted cloud run could
not start because the project's `MaxSimulationTextTurns` quota was exhausted.
No real phone call was made. The console/simulation recording fixture tests
controls only and deliberately captures no audio or uploads to Django.

## Who can listen

| Permission | Who has it | Allows |
| :--- | :--- | :--- |
| `calls.recordings` | Organisation admins; location staff for their own locations | Listening |
| `calls.recordings_download` | Organisation admins | Downloading |

- **Isolation:** a call in another organisation, or a location the person does
  not have, is a 404 for the page and for the audio, whatever ID is typed.
- **The access log** records opening a call page with a playable recording
  (`recording.accessed`) and every download (`recording.downloaded`). Storing a
  recording is logged as a system event (`recording.stored`).

## Retention

- **Its own setting.** "Delete call recordings after (days)" under Privacy &
  settings → Retention, 30 days by default, is separate from transcripts. The
  daily retention job deletes the file and keeps a "Deleted" row saying when.
- **Deleting a call** (by hand, or by "Delete call records after") deletes its
  recording file too.

## Why this design, and what else was considered

| Option | Verdict |
| :--- | :--- |
| **LiveKit Egress** (audio-only `StartEgress` with a `MediaSource`) | Good for production, but on LiveKit Cloud it can only write to cloud storage ("local storage isn't available, so a request that resolves to no storage fails"). So even the first test call would need a cloud bucket, and the bucket's keys would be sent to LiveKit with every request. It also bills egress minutes. It remains a sound alternative for production; the call page and Django storage would stay the same |
| **Vobiz native recording** | Documented for calls controlled by Vobiz's own API: `POST …/Call/{call_uuid}/Record/`, MP3 or WAV. Its docs do not say whether it works on calls that only pass through a Vobiz SIP trunk to LiveKit, which is how our calls run. They also do not say where recordings are stored, for how long, or at what price, and the files sit outside our permission model. Not used; ask Vobiz before relying on it |
| **LiveKit Agent insights** (the "Let LiveKit Cloud keep…" switch) | A separate, LiveKit-hosted copy, held in the United States or the EU and seen only in LiveKit's dashboard. It stays its own switch, off by default, and is not our recording |
| **Worker recorder → Django → private storage** (chosen) | Works for the first test call with no cloud account (local storage), and in production with a bucket in India. Only Django holds storage credentials. One code path for inbound and outbound |

Known limit: if the voice worker process dies mid-call, that call's recording
is lost (Egress would keep it). The page then shows "Failed: the recording did
not arrive…".

## Costs (approximate; check the providers' pricing pages)

- **Size.** About 0.24 MB per minute of call at 32 kbit/s. Fernet encryption
  adds about a third, so about 0.32 MB stored per minute.
- **Example.** 1,000 calls of 3 minutes is about 1 GB stored.
- **AWS S3 Standard in Mumbai (`ap-south-1`).** Roughly US$0.025 per GB-month,
  so cents a month at this volume, plus negligible request charges. Reading the
  file back to a Django server outside AWS adds data-transfer charges, roughly
  US$0.11 per GB.
- **SSE-KMS** adds a small charge per KMS key and per request. SSE-S3
  (`AES256`, the default here) is free.
- **No LiveKit charge**: no Egress is used.

## Settings (`.env.local`; all documented in `.env.example`)

| Setting | Needed for | Notes |
| :--- | :--- | :--- |
| `RECORDING_ENCRYPTION_KEYS` | Always | `manage.py recordings key` prints one. Back it up separately: without it no recording can be played. Comma-separated for rotation; the first encrypts |
| `RECORDING_STORAGE` | Always | `local` (default) or `s3` |
| `RECORDING_LOCAL_DIR` | Local | Blank: `private_recordings/` in the project, git-ignored, never served as static files |
| `RECORDING_S3_BUCKET`, `RECORDING_S3_REGION` (`ap-south-1`) | S3 | |
| `RECORDING_S3_ACCESS_KEY_ID`, `RECORDING_S3_SECRET_ACCESS_KEY` | S3 | Blank uses the server's IAM role |
| `RECORDING_S3_KMS_KEY_ID` | S3, optional | Blank: SSE-S3 |
| `RECORDING_S3_ENDPOINT` | S3-compatible services other than AWS | |
| `CALL_EVENTS_URL`, `CALL_EVENTS_SECRET` | The worker's upload | Already used for call reports; the upload address is worked out from `CALL_EVENTS_URL` (or set `CALL_RECORDING_URL`) |

## Steps for the owner's first recorded test call (local storage)

1. **Add the key.** `.\.venv\Scripts\python.exe manage.py recordings key` prints
   a line. Put it in `.env.local` and keep a copy somewhere safe.
2. **Update the database:** `.\.venv\Scripts\python.exe manage.py migrate`.
3. **Check storage:** `.\.venv\Scripts\python.exe manage.py recordings check`
   should say "Recording storage works (local)".
4. **Restart Django and the voice worker.**
5. **Switch it on.** In the dashboard, under Settings → Calls & privacy:
   - Set "Our own call recordings" to "Record approved test calls only, after
     consent".
   - Optionally write the question in Telugu, for example: "ఈ call మా team
     review కోసం record చేయవచ్చా అండి?"
   - Save.
6. **Make the call.** Under Phone calls → Test call, choose your approved number
   and a published AI employee, then Call.
7. **Agree.** Answer. After the opening line you will hear the question; say
   "yes" (or "అవును"). Talk, then hang up.
8. **Listen.** Open Conversations, open that call, and press Play under
   Recording. The transcript, summary, collected details and next steps are on
   the same page.

If the Recording section says "Failed" or "Not recorded", it gives the reason.
The worker's log has a line `call not recorded` or `recording upload failed`
with the cause.

## Production storage in India (when ready)

1. **The bucket.** In AWS (Mumbai, `ap-south-1`), create a bucket, for example
   `<organisation>-call-recordings`:
   - Block all public access.
   - Default encryption: SSE-S3, or SSE-KMS with your key.
   - Optionally a lifecycle rule that deletes objects after the retention
     period, as a second safeguard.
2. **The IAM user (or role).** Allow only `s3:PutObject`, `s3:GetObject` and
   `s3:DeleteObject` on `arn:aws:s3:::<bucket>/*`.
3. **The settings.** Set `RECORDING_STORAGE=s3` and the `RECORDING_S3_*` values
   in the server's secrets, then run `manage.py recordings check`.
4. **Real customer calls.** Agree the legal basis (India's DPDP Act: notice and
   consent) and the exact question wording before choosing "Record any call,
   after the person agrees".
