"""End-to-end tests for the site relay's TLS identity + SPKI pinning
transport: commercial_runtime/sync/site_relay/{tls_identity,pinned_transport}.py.
Task spec: docs/launch-readiness/lan-restaurant-design.md §5 "Layer 1 --
transport".

Deliberately NOT mock-based, unlike test_relay_client.py's fake-Session
idiom (reserved there for *business-logic* tests where the network layer is
incidental). Here the network layer -- an actual TLS handshake against an
actual self-signed certificate -- IS the thing under test, so these tests
spin up a REAL TLS server on loopback (http.server.HTTPServer +
ssl.SSLContext.wrap_socket, the standard stdlib idiom) on an ephemeral port,
in a background thread, torn down in a finally. Mocking the handshake would
only prove a mock behaves as instructed, not that pinning actually works
against a real socket.
"""
from __future__ import annotations

import contextlib
import datetime
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, load_pem_private_key
from cryptography.x509.oid import NameOID

from commercial_runtime.sync.site_relay.pinned_transport import (
    SpkiPinMismatch,
    pinned_session,
)
from commercial_runtime.sync.site_relay.tls_identity import (
    generate_site_tls_identity,
    load_or_create_site_tls_identity,
    spki_pin,
)

# session.verify = False (pinned_transport.pinned_session's deliberate,
# documented replacement of chain verification with SPKI pinning) makes
# urllib3 emit this warning on every real call in this file -- expected and
# not diagnostic of anything these tests check, so it's silenced here rather
# than left to clutter -q output.
pytestmark = pytest.mark.filterwarnings("ignore::urllib3.exceptions.InsecureRequestWarning")


class _OkHandler(BaseHTTPRequestHandler):
    """Minimal handler -- the response content is never the point of these
    tests, only whether the TLS handshake that must precede it is allowed
    to complete or is refused by the pin check."""

    def log_message(self, format, *args):  # noqa: A002 -- stdlib signature
        pass  # keep pytest output free of one line per HTTP request

    def do_GET(self):
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_one_request(self):
        # A rejected pin (test: fails against the wrong pin) means the
        # client aborts mid-handshake/mid-request from its side, which the
        # server sees as an abrupt socket close -- a real condition, not a
        # bug in the handler, and BaseHTTPRequestHandler's default
        # handle_error() prints a full traceback for it straight to stderr.
        # Swallow ONLY that expected shape so intentional-rejection tests
        # don't spam a stack trace into every pytest run.
        try:
            super().handle_one_request()
        except (ConnectionError, OSError):
            pass


@contextlib.contextmanager
def _running_tls_server(cert_path: Path, key_path: Path):
    """Real HTTPS server on an OS-assigned loopback port, serving the given
    cert/key, in a background thread, always torn down."""
    httpd = HTTPServer(("127.0.0.1", 0), _OkHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        yield f"https://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _write_identity(tmp_path: Path, key_pem: bytes, cert_pem: bytes):
    key_path = tmp_path / "server_key.pem"
    cert_path = tmp_path / "server_cert.pem"
    key_path.write_bytes(key_pem)
    cert_path.write_bytes(cert_pem)
    return key_path, cert_path


def _reissue_over_same_key(private_key_pem: bytes) -> bytes:
    """Builds a SECOND, independent self-signed certificate over the SAME
    private key -- simulating the hub reissuing its cert (e.g. new serial,
    new not-valid-before) without a key rotation. Deliberately does not
    reuse generate_site_tls_identity() (which always mints a fresh key) --
    this has to prove the pin survives a genuine reissue over an unchanged
    key, which means constructing that second certificate independently."""
    private_key = load_pem_private_key(private_key_pem, password=None)
    now = datetime.datetime.now(datetime.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Aura Site Relay (reissued)")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(hours=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .sign(private_key, hashes.SHA256())
    )
    return cert.public_bytes(Encoding.PEM)


def test_generate_site_tls_identity_returns_parseable_ec_p256_key_and_self_signed_cert():
    private_key_pem, certificate_pem = generate_site_tls_identity(valid_days=30)

    private_key = load_pem_private_key(private_key_pem, password=None)
    assert isinstance(private_key.curve, ec.SECP256R1)

    cert = x509.load_pem_x509_certificate(certificate_pem)
    assert cert.issuer == cert.subject  # self-signed
    span = cert.not_valid_after_utc - cert.not_valid_before_utc
    # "roughly" valid_days: generate_site_tls_identity backdates
    # not-valid-before by an hour for clock-skew tolerance (see its
    # docstring), so the span is a little over 30 days, never less.
    assert datetime.timedelta(days=30) <= span <= datetime.timedelta(days=31)


def test_spki_pin_is_stable_and_differs_across_independent_identities():
    _, cert_a_pem = generate_site_tls_identity()
    pin_a_first = spki_pin(cert_a_pem)
    pin_a_second = spki_pin(cert_a_pem)
    assert pin_a_first == pin_a_second

    _, cert_b_pem = generate_site_tls_identity()
    pin_b = spki_pin(cert_b_pem)
    assert pin_a_first != pin_b


def test_reissued_cert_over_the_same_key_has_the_same_pin():
    """The property the whole SPKI-over-whole-certificate pinning decision
    is justified by (design doc §5): reissuing a certificate over the SAME
    private key -- e.g. because the hub's IP changed on a DHCP lease
    renewal -- must not change what a paired device trusts. If this
    regresses, the doc's IP-SAN-changes-on-DHCP-reboot argument is false."""
    private_key_pem, cert_1_pem = generate_site_tls_identity()
    pin_1 = spki_pin(cert_1_pem)

    cert_2_pem = _reissue_over_same_key(private_key_pem)
    pin_2 = spki_pin(cert_2_pem)

    assert pin_2 == pin_1


def test_pinned_session_succeeds_against_the_correct_pin(tmp_path):
    key_pem, cert_pem = generate_site_tls_identity(valid_days=30)
    pin = spki_pin(cert_pem)
    key_path, cert_path = _write_identity(tmp_path, key_pem, cert_pem)

    with _running_tls_server(cert_path, key_path) as base_url:
        session = pinned_session(pin, timeout=5)
        response = session.get(base_url + "/")
        assert response.status_code == 200
        assert response.content == b"ok"


def test_pinned_session_fails_closed_against_the_wrong_pin(tmp_path):
    key_pem, cert_pem = generate_site_tls_identity(valid_days=30)
    _, other_cert_pem = generate_site_tls_identity(valid_days=30)
    wrong_pin = spki_pin(other_cert_pem)
    key_path, cert_path = _write_identity(tmp_path, key_pem, cert_pem)

    with _running_tls_server(cert_path, key_path) as base_url:
        session = pinned_session(wrong_pin, timeout=5)
        # Asserting on the TYPE, not a substring of a message: a pin failure
        # that surfaces as some other (possibly retryable) transport error
        # is exactly the bug pinned_transport.py's send() override exists to
        # prevent -- see its docstring.
        with pytest.raises(SpkiPinMismatch):
            session.get(base_url + "/")


def test_load_or_create_site_tls_identity_is_idempotent(tmp_path):
    key_path_1, cert_path_1, pin_1 = load_or_create_site_tls_identity(tmp_path)
    key_bytes_1 = key_path_1.read_bytes()
    key_mtime_1 = key_path_1.stat().st_mtime_ns

    key_path_2, cert_path_2, pin_2 = load_or_create_site_tls_identity(tmp_path)

    assert key_path_2 == key_path_1
    assert cert_path_2 == cert_path_1
    assert pin_2 == pin_1
    # Not merely "same content" but genuinely never rewritten -- regenerating
    # would silently invalidate every paired device's pin at once (see
    # load_or_create_site_tls_identity's docstring).
    assert key_path_2.read_bytes() == key_bytes_1
    assert key_path_2.stat().st_mtime_ns == key_mtime_1
