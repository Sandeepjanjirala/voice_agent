"""The one place that sends an HTTP request to an outside service.

Every call goes through `send()`: it checks the destination (safety.py),
applies a timeout, caps the response size, and never raises anything but
TransportError. Tests replace `send` itself, so no test reaches the network.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import safety

MAX_TIMEOUT = 10.0
MAX_RESPONSE_BYTES = 256_000


class TransportError(Exception):
    """The request did not complete. The message is safe to show and log."""


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes

    def json(self):
        try:
            return json.loads(self.body or b"null")
        except ValueError:
            return None


def send(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 8.0,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> Response:
    safety.check_destination(url)
    request = urllib.request.Request(
        url, data=body, headers=headers or {}, method=method
    )
    try:
        with urllib.request.urlopen(
            request, timeout=min(timeout, MAX_TIMEOUT)
        ) as response:
            return Response(response.status, response.read(max_bytes))
    except urllib.error.HTTPError as error:
        return Response(error.code, error.read(max_bytes) if error.fp else b"")
    except (OSError, ValueError) as error:
        raise TransportError(
            f"The service could not be reached ({type(error).__name__})."
        ) from None
