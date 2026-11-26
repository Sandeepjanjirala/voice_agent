"""Call recordings in Django (apps/calling/recordings.py): which calls may be
recorded, receiving the worker's signed upload, encrypted storage, playback
through short-lived links, isolation, the access log, failures and deletion.

The audio is a real Ogg Opus file made in the test; storage is a temporary
folder (and a stubbed S3 client). No LiveKit, no network, no real call.
"""

# The fixtures imported from other test modules are named as test parameters.
# ruff: noqa: F811

import io
import json
import time
from datetime import timedelta

import av
import numpy as np
import pytest
from cryptography.fernet import Fernet
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.calling import recordings
from apps.calling.models import CallRecording, PhoneNumber
from apps.crm import retention
from apps.telephony.models import CallRecord
from apps.tenancy.models import AuditEvent
from voice_agent.recording import sign_upload

from .test_calling import employee_for, inbound_post
from .test_calls import MOBILE, SECRET, env, gateway, post_event  # noqa: F401
from .test_tenancy_crm import ASHA, World, assert_no_full_number, body

pytestmark = pytest.mark.django_db

LINE = "+914012345610"


@pytest.fixture(autouse=True)
def private_storage(settings, tmp_path):
    settings.RECORDING_STORAGE = "local"
    settings.RECORDING_LOCAL_DIR = tmp_path / "recordings"
    settings.RECORDING_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
    return tmp_path / "recordings"


@pytest.fixture
def world(env, gateway):
    cache.clear()
    world = World()
    world.org_a.recording = "test"
    world.org_a.recording_notice = "May I record this call?"
    world.org_a.transcripts = "keep"
    world.org_a.save()
    # Test calls are placed from the Phone calls page, which needs staff rights.
    world.admin_a.is_staff = True
    world.admin_a.save()
    return world


@pytest.fixture(scope="module")
def ogg():
    """One second of real stereo Ogg Opus: a tone on each side."""
    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="ogg") as container:
        stream = container.add_stream("libopus", rate=48000, layout="stereo")
        t = np.arange(48000) / 48000
        left = np.sin(2 * np.pi * 440 * t)
        right = np.sin(2 * np.pi * 880 * t)
        pcm = (np.stack([left, right]) * 12000).astype(np.int16)
        for start in range(0, 48000, 960):
            frame = av.AudioFrame.from_ndarray(
                pcm[:, start : start + 960].T.reshape(1, -1),
                format="s16",
                layout="stereo",
            )
            frame.sample_rate = 48000
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


def place_test_call(world):
    """An approved test call with a published employee, placed from the Phone
    calls page as the owner places one."""
    employee = getattr(world, "employee", None) or employee_for(world)
    world.employee = employee
    # One call at a time, and one start every few seconds: settle the last one.
    CallRecord.objects.filter(status__in=CallRecord.ACTIVE).update(
        status=CallRecord.Status.COMPLETED, ended_at=timezone.now()
    )
    cache.clear()
    operator = world.client(world.admin_a)
    response = operator.post(
        reverse("telephony:calls"),
        {"confirm": True, "slot": 0, "employee": str(employee.pk)},
        content_type="application/json",
    )
    assert response.status_code == 201, response.content
    return (
        CallRecord.objects.filter(direction="outbound").order_by("-created_at").first()
    )


def inbound(world, caller, room):
    PhoneNumber.objects.get_or_create(
        organisation=world.org_a,
        campus=world.north,
        number=LINE,
        defaults={"employee": employee_for(world), "routing_enabled": True},
    )
    response = inbound_post({"room": room, "called": LINE, "caller": caller})
    assert response.status_code == 200
    return CallRecord.objects.get(room=room), response.json()


def worker_event(record, **fields):
    return post_event(Client(), {"room": record.room, "event": "recording", **fields})


def upload(
    record,
    data,
    *,
    duration="1.00",
    offset="3.20",
    secret=SECRET,
    timestamp=None,
    room=None,
):
    timestamp = timestamp or str(int(time.time()))
    room = room or record.room
    signature = sign_upload(secret, timestamp, room, duration, offset, data)
    return Client().generic(
        "POST",
        reverse("telephony:call-recording"),
        data,
        content_type="audio/ogg",
        HTTP_X_CALL_TIMESTAMP=timestamp,
        HTTP_X_CALL_SIGNATURE=signature,
        HTTP_X_CALL_ROOM=room,
        HTTP_X_RECORDING_DURATION=duration,
        HTTP_X_RECORDING_OFFSET=offset,
    )


def recorded(world, ogg):
    """A test call that the person agreed to record, with its audio stored."""
    record = place_test_call(world)
    assert (
        post_event(Client(), {"room": record.room, "event": "answered"}).status_code
        == 204
    )
    assert worker_event(record, state="started", offset=3.2).status_code == 204
    assert upload(record, ogg).status_code == 201
    post_event(
        Client(),
        {
            "room": record.room,
            "event": "ended",
            "outcome": "completed",
            "talk_seconds": 20,
            "transcript": [
                {
                    "role": "assistant",
                    "text": "Hello. May I record this call?",
                    "t": 0.3,
                },
                {"role": "user", "text": "Yes, that is fine.", "t": 3.0},
                {"role": "assistant", "text": "Thank you.", "t": 4.1},
            ],
        },
    )
    record.refresh_from_db()
    return record


# --- which calls may be recorded --------------------------------------------------------


def test_an_approved_test_call_may_be_recorded_and_the_worker_is_asked_to(
    world, gateway
):
    record = place_test_call(world)
    assert record.context_snapshot["recording"] == {"allowed": True, "reason": ""}
    (request,) = gateway.dispatches
    profile = json.loads(request.metadata)["agent_profile"]
    assert profile["recording"] == {"notice": "May I record this call?"}


@pytest.mark.parametrize(
    ("mode", "caller", "allowed", "reason"),
    [
        ("off", MOBILE, False, "switched off"),
        ("test", ASHA, False, "Only approved test calls"),
        ("test", MOBILE, True, ""),
        ("consent", ASHA, True, ""),
    ],
)
def test_which_inbound_calls_may_be_recorded(world, mode, caller, allowed, reason):
    world.org_a.recording = mode
    world.org_a.save()
    record, profile = inbound(world, caller, f"in-{mode}-{caller[-2:]}")
    decision = record.context_snapshot["recording"]
    assert decision["allowed"] is allowed and reason in decision["reason"]
    assert ("recording" in profile["agent_profile"]) is allowed


# --- receiving the audio ----------------------------------------------------------------


def test_after_a_yes_the_audio_is_stored_encrypted_and_belongs_to_the_call(
    world, ogg, private_storage
):
    record = recorded(world, ogg)
    recording = CallRecording.objects.get(call=record)
    assert recording.status == "available" and recording.organisation == world.org_a
    assert recording.offset_seconds == 3.2 and recording.duration_seconds == 1.0
    assert recording.size_bytes == len(ogg) and recording.consented_at is not None
    (stored,) = list(private_storage.rglob("*.enc"))
    assert str(record.organisation_id) in str(stored) and str(record.pk) in stored.name
    assert not stored.read_bytes().startswith(b"OggS"), "stored encrypted"
    assert recordings.read(recording) == ogg
    # A retried upload of the same file is accepted once; a different file is refused.
    assert upload(record, ogg).status_code == 201
    assert upload(record, ogg + b"x").status_code == 409


def test_uploads_without_consent_or_permission_or_signature_are_refused(world, ogg):
    record = place_test_call(world)
    assert worker_event(record, state="declined").status_code == 204
    assert CallRecording.objects.get(call=record).status == "not_recorded"
    assert upload(record, ogg).status_code == 409
    other = _unrecordable_call(world)
    assert worker_event(other, state="started").status_code == 204
    assert not CallRecording.objects.filter(call=other).exists(), (
        "ignored for a call that may not be recorded"
    )
    assert upload(other, ogg).status_code == 403
    fresh = place_test_call(world)
    worker_event(fresh, state="started")
    assert upload(fresh, ogg, secret="wrong-secret-" * 4).status_code == 403
    assert upload(fresh, ogg, timestamp=str(int(time.time()) - 900)).status_code == 403
    assert upload(fresh, b"not audio at all").status_code == 400
    assert CallRecording.objects.get(call=fresh).status == "recording"


def test_a_lost_started_event_does_not_lose_the_recording(world, ogg):
    """The worker uploads only after the person agreed, and the upload is
    signed; if the "started" event never arrived, the recording is kept."""
    record = place_test_call(world)
    assert upload(record, ogg).status_code == 201
    recording = CallRecording.objects.get(call=record)
    assert recording.status == "available" and recording.consented_at is not None
    # Nothing is kept from an upload that is not audio.
    other = place_test_call(world)
    assert upload(other, b"not audio at all").status_code == 400
    assert not CallRecording.objects.filter(call=other).exists()


def _unrecordable_call(world):
    record = CallRecord.objects.create(
        room=f"other-{time.time_ns()}",
        masked_number="+91********77",
        organisation=world.org_a,
        status=CallRecord.Status.COMPLETED,
        context_snapshot={"recording": {"allowed": False, "reason": "off"}},
    )
    return record


def test_storage_failure_is_reported_not_lost(world, ogg, settings):
    settings.RECORDING_ENCRYPTION_KEYS = []
    record = place_test_call(world)
    worker_event(record, state="started")
    response = upload(record, ogg)
    assert response.status_code == 503
    recording = CallRecording.objects.get(call=record)
    assert (
        recording.status == "failed" and "RECORDING_ENCRYPTION_KEYS" in recording.reason
    )


# --- playback ----------------------------------------------------------------------------


def test_the_call_page_plays_the_recording_through_a_short_lived_link(world, ogg):
    record = recorded(world, ogg)
    admin = world.client(world.admin_a)
    page = admin.get(reverse("conversation-detail", args=[record.pk]))
    rec = page.context["rec"]
    html = body(page)
    for text in (
        "Recording",
        "Available",
        "Left ear: the person",
        "Right ear:",
        "Download",
        "Yes, that is fine.",
    ):
        assert text in html, text
    assert 'data-t="3.0"' in html and 'data-offset="3.2"' in html
    assert_no_full_number(html)
    assert str(record.organisation_id) not in rec["audio_url"], (
        "no storage path in the link"
    )
    whole = admin.get(rec["audio_url"])
    assert whole.status_code == 200 and whole["Content-Type"] == "audio/ogg"
    assert whole.content == ogg and "no-store" in whole["Cache-Control"]
    part = admin.get(rec["audio_url"], HTTP_RANGE="bytes=10-99")
    assert part.status_code == 206 and part.content == ogg[10:100]
    assert part["Content-Range"] == f"bytes 10-99/{len(ogg)}"
    assert (
        admin.get(rec["audio_url"], HTTP_RANGE=f"bytes={len(ogg) + 5}-").status_code
        == 416
    )
    download = admin.get(rec["download_url"])
    assert download.status_code == 200 and download["Content-Disposition"].startswith(
        "attachment"
    )
    assert "+91" not in download["Content-Disposition"]
    actions = list(AuditEvent.objects.values_list("action", flat=True))
    assert "recording.accessed" in actions and "recording.downloaded" in actions


def test_links_are_personal_expire_and_cannot_be_turned_into_downloads(
    world, ogg, monkeypatch
):
    record = recorded(world, ogg)
    admin = world.client(world.admin_a)
    rec = admin.get(reverse("conversation-detail", args=[record.pk])).context["rec"]
    staff = world.client(world.staff_north)
    record.campus = world.north
    record.save(update_fields=["campus"])
    # Another person's link does not work for them.
    assert staff.get(rec["audio_url"]).status_code == 403
    # A listening link is not a download link.
    assert admin.get(rec["audio_url"] + "&download=1").status_code == 403
    monkeypatch.setattr(recordings, "LINK_SECONDS", -1)
    assert admin.get(rec["audio_url"]).status_code == 403


def test_campus_staff_listen_but_only_admins_download_and_others_never_see_it(
    world, ogg
):
    record = recorded(world, ogg)
    CallRecord.objects.filter(pk=record.pk).update(campus=world.north)
    url = reverse("conversation-detail", args=[record.pk])
    staff = world.client(world.staff_north)
    rec = staff.get(url).context["rec"]
    assert "audio_url" in rec and "download_url" not in rec
    assert staff.get(rec["audio_url"]).status_code == 200
    assert (
        staff.get(
            reverse("conversation-recording", args=[record.pk]) + "?download=1&t=x"
        ).status_code
        == 403
    )
    # Another organisation, or a campus the person does not have: as if it did not exist.
    assert world.client(world.admin_b).get(url).status_code == 404
    assert world.client(world.admin_b).get(rec["audio_url"]).status_code == 404
    CallRecord.objects.filter(pk=record.pk).update(campus=world.south)
    assert staff.get(url).status_code == 404
    assert staff.get(rec["audio_url"]).status_code == 404


# --- when there is nothing to play ----------------------------------------------------


def test_the_page_says_why_there_is_no_recording(world, ogg):
    admin = world.client(world.admin_a)

    def note(record):
        return admin.get(reverse("conversation-detail", args=[record.pk])).context[
            "rec"
        ]["note"]

    declined = place_test_call(world)
    worker_event(declined, state="declined")
    _end(declined)
    assert "did not agree" in note(declined)

    failed = place_test_call(world)
    worker_event(failed, state="failed", reason="the call has no audio to record")
    _end(failed)
    assert "the call has no audio to record" in note(failed)

    lost = place_test_call(world)
    worker_event(lost, state="started")
    _end(lost, ended_ago=timedelta(minutes=20))
    assert "did not arrive" in note(lost)
    assert CallRecording.objects.get(call=lost).status == "failed"

    unasked = place_test_call(world)
    post_event(Client(), {"room": unasked.room, "event": "answered"})
    _end(unasked)
    assert "was not asked, or did not answer" in note(unasked)

    world.org_a.recording = "off"
    world.org_a.save()
    switched_off = place_test_call(world)
    _end(switched_off)
    assert "switched off" in note(switched_off)
    page = body(admin.get(reverse("conversation-detail", args=[switched_off.pk])))
    assert "<audio" not in page and "Not recorded" in page


def _end(record, ended_ago=timedelta(0)):
    post_event(
        Client(),
        {
            "room": record.room,
            "event": "ended",
            "outcome": "completed",
            "talk_seconds": 5,
        },
    )
    when = timezone.now() - ended_ago
    CallRecord.objects.filter(pk=record.pk).update(ended_at=when, updated_at=when)


# --- deletion ---------------------------------------------------------------------------


def test_retention_deletes_recordings_on_their_own_schedule(
    world, ogg, private_storage
):
    record = recorded(world, ogg)
    policy = retention.policy_for(world.org_a)
    policy.recording_days, policy.transcript_days = 7, 0
    policy.save()
    CallRecording.objects.filter(call=record).update(
        created_at=timezone.now() - timedelta(days=8)
    )
    assert retention.preview(world.org_a)["recordings"] == 1
    assert retention.apply(world.org_a)["recordings"] == 1
    recording = CallRecording.objects.get(call=record)
    assert (
        recording.status == "deleted"
        and recording.deleted_at
        and not recording.object_key
    )
    assert not list(private_storage.rglob("*.enc"))
    # The transcript keeps its own schedule.
    assert record.transcript.turns
    page = body(
        world.client(world.admin_a).get(
            reverse("conversation-detail", args=[record.pk])
        )
    )
    assert "Deleted under the organisation" in page and "<audio" not in page


def test_deleting_the_call_deletes_the_file(world, ogg, private_storage):
    record = recorded(world, ogg)
    assert list(private_storage.rglob("*.enc"))
    record.delete()
    assert not list(private_storage.rglob("*.enc"))


# --- storage --------------------------------------------------------------------------


def test_s3_storage_is_private_and_encrypted_on_both_sides(settings):
    import boto3
    from botocore.stub import Stubber

    settings.RECORDING_S3_BUCKET = "school-call-recordings"
    settings.RECORDING_S3_KMS_KEY_ID = "alias/call-recordings"
    client = boto3.client(
        "s3", region_name="ap-south-1", aws_access_key_id="x", aws_secret_access_key="y"
    )
    stub = Stubber(client)
    stub.add_response(
        "put_object",
        {},
        {
            "Bucket": "school-call-recordings",
            "Key": "org/call.ogg.enc",
            "Body": b"sealed",
            "ContentType": "application/octet-stream",
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": "alias/call-recordings",
        },
    )
    stub.add_response(
        "delete_object",
        {},
        {"Bucket": "school-call-recordings", "Key": "org/call.ogg.enc"},
    )
    with stub:
        store = recordings.S3Storage(client=client)
        store.put("org/call.ogg.enc", b"sealed")
        store.delete("org/call.ogg.enc")
    stub.assert_no_pending_responses()


def test_local_storage_never_leaves_its_folder(private_storage):
    store = recordings.LocalStorage(private_storage)
    with pytest.raises(recordings.RecordingError):
        store.put("../../escape.ogg", b"x")


def test_the_setup_command_makes_a_key_shows_status_and_checks_storage(private_storage):
    from django.core.management import call_command

    out = io.StringIO()
    call_command("recordings", "key", stdout=out)
    key = out.getvalue().splitlines()[0].split("=", 1)[1]
    Fernet(key.encode())  # a valid key
    out = io.StringIO()
    call_command("recordings", "status", stdout=out)
    assert (
        "Storage: local" in out.getvalue()
        and "Encryption keys: 1 set" in out.getvalue()
    )
    out = io.StringIO()
    call_command("recordings", "check", stdout=out)
    assert "Recording storage works (local)" in out.getvalue()
    assert not list(private_storage.rglob("*.enc")), "the test file was deleted"


def test_a_superuser_test_call_belongs_to_their_organisation(world, gateway):
    """The owner's case: a platform superuser, working in the organisation
    chosen in the sidebar, tests the line. The call must belong to that
    organisation (else it is hidden from Call results). Without an AI employee
    nothing on the call could ask for consent, so it is never recorded."""
    world.root.is_staff = True
    world.root.save()
    root = world.client(world.root)
    root.post(reverse("organisation-switch"), {"organisation": world.org_a.pk})
    cache.clear()
    response = root.post(
        reverse("telephony:calls"),
        {"confirm": True, "slot": 0},
        content_type="application/json",
    )
    assert response.status_code == 201, response.content
    record = CallRecord.objects.order_by("-created_at").first()
    assert record.organisation == world.org_a
    assert "agent_profile" not in json.loads(gateway.dispatches[-1].metadata)
    assert record.context_snapshot["recording"]["allowed"] is False
    listing = body(root.get(reverse("conversations")))
    assert reverse("conversation-detail", args=[record.pk]) in listing
