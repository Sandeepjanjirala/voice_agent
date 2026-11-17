"""What an action may send, and where.

Every outside request goes to a URL built here: the integration's own base
URL plus a path fixed by an admin, with only validated inputs filled in and
percent-encoded. The URL must be HTTPS, its host must be the integration's
host, and the host must not resolve to a private, loopback or link-local
address. Nothing the model says can change the destination.
"""

import ipaddress
import re
import socket
from urllib.parse import quote, urlsplit

from django.conf import settings

_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]{0,40})\}")


class UnsafeRequestError(ValueError):
    """The request is not allowed. The message is safe to show."""


def check_base_url(url: str) -> str:
    """A base URL an admin may save: https, a host, no credentials or query."""
    parts = urlsplit(url.strip())
    allowed = ("https",) + (("http",) if settings.INTEGRATIONS_ALLOW_LOCAL else ())
    if parts.scheme not in allowed:
        raise UnsafeRequestError("The address must start with https://.")
    if not parts.hostname:
        raise UnsafeRequestError("The address has no host.")
    if parts.username or parts.password:
        raise UnsafeRequestError(
            "Put credentials in the secret fields, not the address."
        )
    if parts.query or parts.fragment:
        raise UnsafeRequestError("The base address cannot have a query or fragment.")
    return url.strip().rstrip("/")


def placeholders(path: str) -> list[str]:
    return _PLACEHOLDER.findall(path)


def build_url(base_url: str, path: str, inputs: dict) -> str:
    """The base URL plus `path`, with each {name} replaced by its validated
    input, percent-encoded so it cannot change the path's shape."""
    base = check_base_url(base_url)
    if not path.startswith("/") or ".." in path or "//" in path:
        raise UnsafeRequestError(
            "The path must start with / and stay below the base address."
        )

    def fill(match: re.Match) -> str:
        name = match.group(1)
        if name not in inputs:
            raise UnsafeRequestError(f"Missing input {name}.")
        return quote(str(inputs[name]), safe="")

    url = base + _PLACEHOLDER.sub(fill, path)
    if urlsplit(url).hostname != urlsplit(base).hostname:
        raise UnsafeRequestError("The request would leave the integration's host.")
    return url


def check_destination(url: str) -> None:
    """Refuse hosts that resolve to private networks (unless local
    development is allowed), so an action cannot reach internal services."""
    if settings.INTEGRATIONS_ALLOW_LOCAL:
        return
    host = urlsplit(url).hostname or ""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        raise UnsafeRequestError(f"{host} could not be found.") from None
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeRequestError(f"{host} is on a private network.")
