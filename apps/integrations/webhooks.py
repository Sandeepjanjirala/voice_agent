"""Outgoing webhooks: tell another system what happened, signed, with retries.

Each event becomes one WebhookDelivery per subscribed webhook, saved in the
same transaction as the change it reports, and sent after commit. A failed
delivery is retried with growing gaps by the scheduled job, then given up and
announced. Payloads carry ids, masked numbers and outcomes: never a full phone
number, a transcript or a secret.

The receiver checks `X-Webhook-Signature`: HMAC-SHA256, with the webhook's
secret, over "<X-Webhook-Timestamp>.<body>".
"""

import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import safety, secrets, transport
from .models import Integration, WebhookDelivery
from .providers import live

EVENTS = {
    "call.analysed": "A call was summarised",
    "lead.created": "A lead was created",
    "lead.stage_changed": "A lead changed stage",
    "appointment.booked": "An appointment was booked",
}
MAX_ATTEMPTS = 6
FIRST_RETRY = timedelta(minutes=1)
Status = WebhookDelivery.Status


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()


def lead_created(lead, source: str) -> list[WebhookDelivery]:
    """A new lead. Names and full numbers are not sent: the receiver gets the
    masked number and IDs to look the rest up in the dashboard."""
    return emit(
        lead.organisation,
        "lead.created",
        {
            "lead": str(lead.pk),
            "contact": str(lead.contact_id),
            "number": lead.contact.phone_masked,
            "campus": lead.campus.name,
            "subject": lead.subject,
            "stage": lead.stage,
            "source": source,
        },
    )


def lead_stage_changed(lead, before: str) -> list[WebhookDelivery]:
    if before == lead.stage:
        return []
    return emit(
        lead.organisation,
        "lead.stage_changed",
        {"lead": str(lead.pk), "from": before, "to": lead.stage},
    )


def emit(organisation, event: str, data: dict) -> list[WebhookDelivery]:
    """Queue `event` for every active webhook subscribed to it."""
    if organisation is None or event not in EVENTS:
        return []
    created = []
    for integration in Integration.objects.filter(
        organisation=organisation, kind=Integration.Kind.WEBHOOK, is_active=True
    ):
        if event not in (integration.config.get("events") or []):
            continue
        delivery = WebhookDelivery.objects.create(
            integration=integration,
            event=event,
            payload={
                "id": str(uuid.uuid4()),
                "event": event,
                "created_at": timezone.now().isoformat(),
                "organisation": str(organisation.pk),
                "data": data,
            },
        )
        created.append(delivery)
        from apps.operations.tasks import deliver_webhook

        transaction.on_commit(lambda pk=delivery.pk: deliver_webhook.delay(pk))
    return created


def deliver(delivery_id: int) -> WebhookDelivery | None:
    """Try one delivery once. Safe to call twice: only a waiting delivery
    that is due is sent, and only by one caller at a time."""
    with transaction.atomic():
        delivery = (
            WebhookDelivery.objects.select_for_update(skip_locked=True)
            .select_related("integration")
            .filter(pk=delivery_id, status=Status.PENDING)
            .first()
        )
        if delivery is None:
            return None
        if delivery.next_attempt_at and delivery.next_attempt_at > timezone.now():
            return delivery
        integration = delivery.integration
        if not live():
            delivery.status = Status.SIMULATED
            delivery.last_error = "Not sent: live integrations are off."
            delivery.save()
            return delivery
        body = json.dumps(delivery.payload, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        secret = secrets.read(integration).get("signing_secret", "")
        delivery.attempts += 1
        try:
            url = safety.check_base_url(integration.config.get("url", ""))
            response = transport.send(
                "POST",
                url,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Event": delivery.event,
                    "X-Webhook-Id": str(delivery.payload.get("id") or delivery.pk),
                    "X-Webhook-Timestamp": timestamp,
                    "X-Webhook-Signature": sign(secret, timestamp, body),
                },
                body=body,
                timeout=8,
            )
            code, error = response.status, ""
        except (transport.TransportError, safety.UnsafeRequestError) as problem:
            code, error = None, str(problem)
        delivery.last_status_code = code
        if code is not None and 200 <= code < 300:
            delivery.status, delivery.delivered_at, delivery.last_error = (
                Status.DELIVERED,
                timezone.now(),
                "",
            )
        else:
            delivery.last_error = (error or f"The receiver answered HTTP {code}.")[:200]
            if delivery.attempts >= MAX_ATTEMPTS:
                delivery.status, delivery.next_attempt_at = Status.FAILED, None
            else:
                delivery.next_attempt_at = timezone.now() + FIRST_RETRY * (
                    2 ** (delivery.attempts - 1)
                )
        delivery.save()
    if delivery.status == Status.FAILED:
        from apps.operations.jobs import notify
        from apps.operations.models import Notification

        notify(
            integration.organisation,
            Notification.Kind.WEBHOOK,
            f"Webhook “{integration.name}” gave up on a {delivery.event} event: "
            f"{delivery.last_error}",
            url=f"/integrations/{integration.pk}/",
            key=f"webhook-failed:{delivery.pk}",
        )
    return delivery


def deliver_due(now=None) -> dict:
    now = now or timezone.now()
    due = WebhookDelivery.objects.filter(status=Status.PENDING).filter(
        next_attempt_at__isnull=True
    ) | WebhookDelivery.objects.filter(status=Status.PENDING, next_attempt_at__lte=now)
    counts: dict[str, int] = {}
    for pk in list(due.values_list("pk", flat=True)[:200]):
        result = deliver(pk)
        if result is not None:
            counts[result.status] = counts.get(result.status, 0) + 1
    return counts
