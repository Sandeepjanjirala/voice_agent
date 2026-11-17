"""What each kind of integration actually does.

Built-in campus calendar: real, local (it is this system's own data).
Google Calendar, WhatsApp Cloud API and approved REST endpoints: each builds
its exact request, and sends it only when INTEGRATIONS_LIVE_ENABLED is on.
Otherwise the run is "simulated": checked fully, shown, logged, and not sent.

Endpoints are the providers' documented ones:
  Google Calendar API v3   POST https://oauth2.googleapis.com/token
                           POST https://www.googleapis.com/calendar/v3/freeBusy
                           POST https://www.googleapis.com/calendar/v3/calendars/{id}/events
  WhatsApp Cloud API       POST https://graph.facebook.com/{version}/{phone id}/messages
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlencode

from django.conf import settings
from django.utils import timezone

from apps.tenancy import hours

from . import safety, secrets, transport
from .models import Appointment

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_URL = "https://www.googleapis.com/calendar/v3"
WHATSAPP_URL = "https://graph.facebook.com"
DEFAULT_WHATSAPP_VERSION = "v21.0"


class ProviderError(Exception):
    """The provider refused or the request is not possible. Safe to show."""


@dataclass
class Outcome:
    status: str  # an ActionRun.Status value
    message: str
    output: dict = field(default_factory=dict)


def live() -> bool:
    return bool(settings.INTEGRATIONS_LIVE_ENABLED)


# --- slots ---------------------------------------------------------------------


def _slots(campus, day: date) -> list[datetime]:
    """Every slot start on a day the campus is open, in the future."""
    window = hours.hours_on(campus, day)
    if window is None:
        return []
    step = timedelta(minutes=max(campus.slot_minutes, 10))
    at = timezone.make_aware(datetime.combine(day, window[0]))
    end = timezone.make_aware(datetime.combine(day, window[1]))
    now = timezone.now()
    found = []
    while at + step <= end:
        if at > now:
            found.append(at)
        at += step
    return found


def _free(slots, busy: list[tuple[datetime, datetime]], step: timedelta):
    return [
        s
        for s in slots
        if not any(s < b_end and s + step > b_start for b_start, b_end in busy)
    ]


def _local_busy(campus, day: date):
    rows = Appointment.objects.filter(
        campus=campus,
        status=Appointment.Status.BOOKED,
        starts_at__date=day,
    ).values_list("starts_at", "ends_at")
    return list(rows)


# --- built-in campus calendar --------------------------------------------------------


def campus_availability(campus, day: date) -> Outcome:
    if not hours.has_hours(campus):
        raise ProviderError(f"{campus.name} has no opening hours set.")
    step = timedelta(minutes=campus.slot_minutes)
    free = _free(_slots(campus, day), _local_busy(campus, day), step)
    return Outcome(
        "succeeded",
        f"{len(free)} free slot{'s' if len(free) != 1 else ''} on {day:%d %b}.",
        {
            "date": day.isoformat(),
            "slots": [timezone.localtime(s).strftime("%H:%M") for s in free],
        },
    )


def campus_book(campus, start: datetime, title: str, lead=None) -> Outcome:
    start = timezone.localtime(
        start if timezone.is_aware(start) else timezone.make_aware(start)
    )
    step = timedelta(minutes=campus.slot_minutes)
    free = _free(_slots(campus, start.date()), _local_busy(campus, start.date()), step)
    if start not in free:
        raise ProviderError(f"{start:%d %b %H:%M} is not a free slot at {campus.name}.")
    appointment = Appointment.objects.create(
        organisation_id=campus.organisation_id,
        campus=campus,
        lead=lead,
        title=title[:160],
        starts_at=start,
        ends_at=start + step,
    )
    return Outcome(
        "succeeded",
        f"Booked {start:%a %d %b, %H:%M} at {campus.name}.",
        {"appointment": str(appointment.pk), "start": start.isoformat()},
    )


# --- Google Calendar -------------------------------------------------------------------


def _google_token(integration) -> str:
    stored = secrets.read(integration)
    body = urlencode(
        {
            "client_id": integration.config.get("client_id", ""),
            "client_secret": stored.get("client_secret", ""),
            "refresh_token": stored.get("refresh_token", ""),
            "grant_type": "refresh_token",
        }
    ).encode()
    response = transport.send(
        "POST",
        GOOGLE_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=body,
    )
    token = (
        (response.json() or {}).get("access_token") if response.status == 200 else None
    )
    if not token:
        raise ProviderError(
            f"Google did not issue an access token (HTTP {response.status})."
        )
    return token


def google_availability(integration, campus, day: date) -> Outcome:
    calendar = integration.config.get("calendar_id", "")
    start = timezone.make_aware(datetime.combine(day, datetime.min.time()))
    request = {
        "timeMin": start.isoformat(),
        "timeMax": (start + timedelta(days=1)).isoformat(),
        "timeZone": settings.TIME_ZONE,
        "items": [{"id": calendar}],
    }
    if not live():
        return Outcome(
            "simulated",
            "Not sent: live integrations are off. This is the availability request.",
            {
                "request": {
                    "method": "POST",
                    "url": f"{GOOGLE_CALENDAR_URL}/freeBusy",
                    "json": request,
                }
            },
        )
    token = _google_token(integration)
    response = transport.send(
        "POST",
        f"{GOOGLE_CALENDAR_URL}/freeBusy",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        body=json.dumps(request).encode(),
    )
    data = response.json() or {}
    if response.status != 200:
        raise ProviderError(f"Google Calendar answered HTTP {response.status}.")
    busy = [
        (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
        for b in data.get("calendars", {}).get(calendar, {}).get("busy", [])
    ]
    step = timedelta(minutes=campus.slot_minutes)
    free = _free(_slots(campus, day), busy, step)
    return Outcome(
        "succeeded",
        f"{len(free)} free slots on {day:%d %b}.",
        {
            "date": day.isoformat(),
            "slots": [timezone.localtime(s).strftime("%H:%M") for s in free],
        },
    )


def google_book(integration, campus, start: datetime, title: str, lead=None) -> Outcome:
    calendar = integration.config.get("calendar_id", "")
    start = timezone.localtime(
        start if timezone.is_aware(start) else timezone.make_aware(start)
    )
    end = start + timedelta(minutes=campus.slot_minutes)
    event = {
        "summary": title[:160],
        "start": {"dateTime": start.isoformat(), "timeZone": settings.TIME_ZONE},
        "end": {"dateTime": end.isoformat(), "timeZone": settings.TIME_ZONE},
    }
    url = f"{GOOGLE_CALENDAR_URL}/calendars/{quote(calendar, safe='')}/events"
    if not live():
        return Outcome(
            "simulated",
            "Not booked: live integrations are off. This is the booking request.",
            {"request": {"method": "POST", "url": url, "json": event}},
        )
    token = _google_token(integration)
    response = transport.send(
        "POST",
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        body=json.dumps(event).encode(),
    )
    if response.status not in (200, 201):
        raise ProviderError(f"Google Calendar answered HTTP {response.status}.")
    external = (response.json() or {}).get("id", "")
    Appointment.objects.create(
        organisation_id=campus.organisation_id,
        campus=campus,
        lead=lead,
        title=title[:160],
        starts_at=start,
        ends_at=end,
        external_id=str(external)[:200],
    )
    return Outcome(
        "succeeded",
        f"Booked {start:%a %d %b, %H:%M} in Google Calendar.",
        {"event": external},
    )


# --- WhatsApp Cloud API ------------------------------------------------------------------


def whatsapp_template(integration, action, contact, parameters: list[str]) -> Outcome:
    """Send an approved template to a contact who agreed to WhatsApp messages."""
    template = action.settings.get("template", "")
    approved = integration.config.get("templates") or []
    if template not in approved:
        raise ProviderError(
            f"Template “{template}” is not on this account's approved list."
        )
    if not contact.whatsapp_consent:
        raise ProviderError("The contact has not agreed to WhatsApp messages.")
    if contact.is_erased:
        raise ProviderError("The contact was erased.")
    from apps.crm import crypto
    from apps.telephony.masking import mask_phone

    number = crypto.decrypt(contact.phone_encrypted)
    version = integration.config.get("api_version") or DEFAULT_WHATSAPP_VERSION
    if not re.fullmatch(r"v\d{1,2}\.\d", version):
        raise ProviderError("The API version must look like v21.0.")
    phone_id = str(integration.config.get("phone_number_id", ""))
    if not phone_id.isdigit():
        raise ProviderError("The WhatsApp phone number ID is not set.")
    message = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number.lstrip("+"),
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": action.settings.get("language", "en")},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": p[:200]} for p in parameters
                    ],
                }
            ]
            if parameters
            else [],
        },
    }
    url = f"{WHATSAPP_URL}/{version}/{phone_id}/messages"
    shown = {**message, "to": mask_phone(number)}
    if not live():
        return Outcome(
            "simulated",
            "Not sent: live integrations are off. This is the message that would be sent.",
            {"request": {"method": "POST", "url": url, "json": shown}},
        )
    token = secrets.read(integration).get("access_token", "")
    response = transport.send(
        "POST",
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        body=json.dumps(message).encode(),
    )
    data = response.json() or {}
    if response.status != 200:
        error = (
            (data.get("error") or {}).get("message", "")
            if isinstance(data, dict)
            else ""
        )
        raise ProviderError(f"WhatsApp answered HTTP {response.status}. {error}"[:200])
    sent = (data.get("messages") or [{}])[0].get("id", "")
    return Outcome(
        "succeeded",
        f"WhatsApp template sent to {mask_phone(number)}.",
        {"message": sent},
    )


# --- approved REST endpoint -------------------------------------------------------------


def rest_request(integration, action, inputs: dict) -> Outcome:
    method = action.settings.get("method", "GET")
    if method not in ("GET", "POST"):
        raise ProviderError("Only GET and POST requests are allowed.")
    url = safety.build_url(
        integration.config.get("base_url", ""), action.settings.get("path", "/"), inputs
    )
    body_fields = [
        name for name in action.settings.get("body_fields", []) if name in inputs
    ]
    body = {name: inputs[name] for name in body_fields} if method == "POST" else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    shown = {"method": method, "url": url, "json": body}
    header = integration.config.get("auth_header", "")
    if header:
        headers[header] = "…"
    if not live():
        return Outcome(
            "simulated",
            "Not sent: live integrations are off. This is the request.",
            {"request": shown},
        )
    token = secrets.read(integration).get("token", "")
    if header:
        scheme = integration.config.get("auth_scheme", "")
        headers[header] = f"{scheme} {token}".strip()
    response = transport.send(
        method,
        url,
        headers=headers,
        body=json.dumps(body).encode() if body is not None else None,
        timeout=float(action.settings.get("timeout", 8)),
    )
    data = response.json()
    keep = action.settings.get("response_fields") or []
    output = {"status": response.status}
    if isinstance(data, dict):
        output["data"] = {name: data.get(name) for name in keep if name in data}
    if not 200 <= response.status < 300:
        raise ProviderError(f"The service answered HTTP {response.status}.")
    return Outcome("succeeded", f"The service answered HTTP {response.status}.", output)
