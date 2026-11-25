"""The one place that sends an HTTP request to an outside service.

Every call goes through `send()`. It resolves the host once and refuses
private addresses (safety.py), then connects to exactly the address it checked
(no second DNS lookup, so DNS rebinding cannot redirect it), never follows a
redirect, applies a timeout and caps the response size. It raises only
TransportError or safety.UnsafeRequestError. Feature tests replace `send`
itself; tests/django/test_transport_safety.py tests `send` against loopback.
"""

import http.client
import json
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

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
    """One request to the checked address. Redirects are never followed: a 3xx
    answer is returned as it is, and callers treat anything but 2xx as failed."""
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http"):
        raise TransportError("Only http and https addresses can be reached.")
    address = safety.resolve_destination(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    timeout = min(timeout, MAX_TIMEOUT)
    connection = (
        _PinnedHTTPS(parts.hostname, port, address, timeout)
        if parts.scheme == "https"
        else _PinnedHTTP(parts.hostname, port, address, timeout)
    )
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return Response(response.status, response.read(max_bytes))
    except (OSError, http.client.HTTPException, ValueError) as error:
        raise TransportError(
            f"The service could not be reached ({type(error).__name__})."
        ) from None
    finally:
        connection.close()


class _PinnedHTTP(http.client.HTTPConnection):
    """Connects to the address that was checked, whatever DNS says now."""

    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    """Like _PinnedHTTP, with TLS verified against the real host name."""

    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        super().__init__(
            host, port, timeout=timeout, context=ssl.create_default_context()
        )
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
