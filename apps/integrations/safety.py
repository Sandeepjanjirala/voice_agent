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


def _is_public(ip) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def resolve_destination(url: str) -> str:
    """The one IP address the request will connect to.

    The host is resolved once, and every address it resolves to must be public
    (unless local development is allowed). The transport then connects to this
    address and no other, so a second DNS answer (DNS rebinding) cannot send
    the request to an internal service after the check."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        raise UnsafeRequestError(f"{host} could not be found.") from None
    addresses = [info[4][0] for info in infos]
    if not addresses:
        raise UnsafeRequestError(f"{host} could not be found.")
    if not settings.INTEGRATIONS_ALLOW_LOCAL:
        for address in addresses:
            if not _is_public(ipaddress.ip_address(address.split("%")[0])):
                raise UnsafeRequestError(f"{host} is on a private network.")
    return addresses[0]


def check_destination(url: str) -> None:
    """Refuse hosts that resolve to private networks (unless local
    development is allowed), so an action cannot reach internal services."""
    resolve_destination(url)
