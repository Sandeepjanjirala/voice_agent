"""The integrations transport: no redirects followed, no second DNS lookup
(DNS rebinding), no private addresses, TLS checked against the real host name.

Real sockets on loopback for the redirect test (local development mode); the
rebinding and TLS tests replace DNS and the socket layer, so nothing leaves
the machine."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from apps.integrations import safety, transport

PUBLIC = "93.184.216.34"


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_a_redirect_is_returned_not_followed(settings):
    settings.INTEGRATIONS_ALLOW_LOCAL = True
    hits = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    target = serve(Target)

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header(
                "Location", f"http://127.0.0.1:{target.server_port}/internal"
            )
            self.end_headers()

        def log_message(self, *args):
            pass

    redirect = serve(Redirect)
    try:
        response = transport.send(
            "GET",
            f"http://127.0.0.1:{redirect.server_port}/start",
            headers={"Authorization": "Bearer secret"},
        )
    finally:
        redirect.shutdown()
        target.shutdown()
    assert response.status == 302
    assert hits == [], "the redirect target was never contacted"


class Resolver:
    """getaddrinfo that answers differently each time it is asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = 0

    def __call__(self, host, port, *args, **kwargs):
        self.asked += 1
        address = self.answers.pop(0) if self.answers else "127.0.0.1"
        return [(2, 1, 6, "", (address, port))]


def test_dns_rebinding_cannot_move_the_connection(monkeypatch, settings):
    settings.INTEGRATIONS_ALLOW_LOCAL = False
    resolver = Resolver(PUBLIC, "127.0.0.1")
    monkeypatch.setattr(safety.socket, "getaddrinfo", resolver)
    connected = []

    def create_connection(address, timeout=None):
        connected.append(address)
        raise OSError("no network in tests")

    monkeypatch.setattr(transport.socket, "create_connection", create_connection)
    with pytest.raises(transport.TransportError):
        transport.send("POST", "https://hooks.example.org/in", body=b"{}")
    assert resolver.asked == 1, "the host is resolved once"
    assert connected == [(PUBLIC, 443)], "the checked address, never a second answer"


@pytest.mark.parametrize(
    "addresses",
    [["10.0.0.5"], [PUBLIC, "127.0.0.1"], ["169.254.169.254"], ["::1"], ["fd00::1"]],
)
def test_any_private_answer_refuses_the_request(monkeypatch, settings, addresses):
    settings.INTEGRATIONS_ALLOW_LOCAL = False
    monkeypatch.setattr(
        safety.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", (a, port)) for a in addresses],
    )

    def no_connection(*args, **kwargs):
        raise AssertionError("must not connect")

    monkeypatch.setattr(transport.socket, "create_connection", no_connection)
    with pytest.raises(safety.UnsafeRequestError):
        transport.send("GET", "https://api.example.org/x")


def test_tls_is_checked_against_the_real_host_name(monkeypatch, settings):
    settings.INTEGRATIONS_ALLOW_LOCAL = False
    monkeypatch.setattr(
        safety.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", (PUBLIC, port))],
    )
    monkeypatch.setattr(transport.socket, "create_connection", lambda *a, **k: object())
    wrapped = []

    class Context:
        def wrap_socket(self, sock, server_hostname=None):
            wrapped.append(server_hostname)
            raise OSError("handshake stopped in tests")

    monkeypatch.setattr(transport.ssl, "create_default_context", Context)
    with pytest.raises(transport.TransportError):
        transport.send("GET", "https://calendar.example.org:8443/x")
    assert wrapped == ["calendar.example.org"]


@pytest.mark.parametrize(
    "url", ["ftp://example.org/x", "file:///etc/passwd", "gopher://x"]
)
def test_only_http_and_https(url):
    with pytest.raises(transport.TransportError):
        transport.send("GET", url)
