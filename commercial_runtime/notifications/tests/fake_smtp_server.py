"""Test-only fixture: a minimal, real SMTP server over a real TCP socket.

Not a mock of smtplib -- `smtp_client.send_email()` opens a genuine
`socket`/`smtplib.SMTP` connection to this server and speaks the real SMTP
wire protocol (EHLO, MAIL FROM, RCPT TO, DATA, QUIT) against it, so tests
using this fixture exercise smtp_client.py's actual network code path, not a
monkeypatched stand-in for it.

Python 3.12+ removed the stdlib `smtpd` module this repo's task
instructions originally suggested reaching for
(`python -m smtpd -c DebuggingServer`), and `aiosmtpd` is not a project
dependency (adding one for tests only would contradict the "no new heavy
dependency" instruction this whole feature was built under) -- this file is
the small, dependency-free replacement: just enough of the SMTP command
subset (EHLO/HELO, MAIL FROM, RCPT TO, DATA, QUIT, NOOP) for smtplib's
client behavior to complete a real send.

By default STARTTLS/AUTH are NOT offered, so every older test (which sets
AURA_SMTP_USE_TLS=0 and leaves AURA_SMTP_USER unset) behaves exactly as
before. Real-email wave: pass `tls_context=` to ALSO offer STARTTLS, and
`credentials=(user, password)` to require AUTH PLAIN (offered only after TLS,
like a real submission server). That is what lets a test prove the sender
actually called starttls() and login() -- the gap this fixture used to leave
(the product's TLS+AUTH path had zero coverage). `require_tls=True` refuses
MAIL before STARTTLS with 530, so a sender that skipped TLS fails loudly
instead of "succeeding" on a plaintext connection.

What the server records, for assertions:
  .received       one dict per accepted message: mail_from, rcpt_to, data, plus
                  `tls` (the session was upgraded) and `auth_user` (who logged in)
  .starttls_count how many STARTTLS upgrades completed
  .auth_attempts  [(user, password, accepted_bool)] -- fake test credentials only
"""
from __future__ import annotations

import base64
import datetime
import ipaddress
import socketserver
import ssl
import threading
from pathlib import Path


class _SMTPHandler(socketserver.StreamRequestHandler):
    def _send(self, line: bytes) -> None:
        self.wfile.write(line)
        self.wfile.flush()

    def _ehlo_reply(self, tls: bool) -> bytes:
        server = self.server
        features = []
        if server.tls_context is not None and not tls:
            features.append("STARTTLS")
        if server.credentials is not None and (tls or server.tls_context is None):
            features.append("AUTH PLAIN")
        lines = ["fake.smtp.test"] + features
        return "".join(
            f"250{'-' if i < len(lines) - 1 else ' '}{text}\r\n" for i, text in enumerate(lines)
        ).encode()

    def handle(self):
        server = self.server
        self._send(b'220 fake.smtp.test ESMTP\r\n')
        mail_from = None
        rcpt_to = []
        data_lines = []
        in_data = False
        tls = False
        auth_user = None

        while True:
            line = self.rfile.readline()
            if not line:
                break

            if in_data:
                if line.rstrip(b'\r\n') == b'.':
                    in_data = False
                    server.received.append({
                        'mail_from': mail_from,
                        'rcpt_to': list(rcpt_to),
                        'data': b''.join(data_lines).decode('utf-8', errors='replace'),
                        'tls': tls,
                        'auth_user': auth_user,
                    })
                    mail_from, rcpt_to, data_lines = None, [], []
                    self._send(b'250 OK: queued\r\n')
                else:
                    # RFC 5321 dot-stuffing: a leading '.' on a real content
                    # line is escaped as '..' by the client; undo that here.
                    if line.startswith(b'..'):
                        line = line[1:]
                    data_lines.append(line)
                continue

            cmd = line.decode('utf-8', errors='replace').strip()
            upper = cmd.upper()
            if upper.startswith('EHLO'):
                self._send(self._ehlo_reply(tls))
            elif upper.startswith('HELO'):
                self._send(b'250 fake.smtp.test\r\n')
            elif upper.startswith('STARTTLS') and server.tls_context is not None and not tls:
                self._send(b'220 Ready to start TLS\r\n')
                try:
                    wrapped = server.tls_context.wrap_socket(self.connection, server_side=True)
                except (ssl.SSLError, OSError):
                    break
                self.connection = wrapped
                self.rfile = wrapped.makefile('rb')
                self.wfile = wrapped.makefile('wb', 0)
                tls = True
                server.starttls_count += 1
                mail_from, rcpt_to, data_lines, auth_user = None, [], [], None
            elif upper.startswith('AUTH PLAIN') and server.credentials is not None:
                parts = cmd.split(None, 2)
                try:
                    _authzid, user, password = base64.b64decode(parts[2]).decode().split('\0')
                except Exception:
                    self._send(b'501 bad auth\r\n')
                    continue
                accepted = (user, password) == server.credentials and (tls or server.tls_context is None)
                server.auth_attempts.append((user, password, accepted))
                if accepted:
                    auth_user = user
                    self._send(b'235 2.7.0 Authentication successful\r\n')
                else:
                    self._send(b'535 5.7.8 Authentication credentials invalid\r\n')
            elif upper.startswith('MAIL FROM'):
                if server.require_tls and not tls:
                    self._send(b'530 5.7.0 Must issue a STARTTLS command first\r\n')
                elif server.credentials is not None and auth_user is None:
                    self._send(b'530 5.7.0 Authentication required\r\n')
                else:
                    mail_from = cmd
                    self._send(b'250 OK\r\n')
            elif upper.startswith('RCPT TO'):
                rcpt_to.append(cmd)
                self._send(b'250 OK\r\n')
            elif upper.startswith('DATA'):
                in_data = True
                self._send(b'354 End data with <CR><LF>.<CR><LF>\r\n')
            elif upper.startswith('QUIT'):
                self._send(b'221 Bye\r\n')
                break
            elif upper.startswith('NOOP'):
                self._send(b'250 OK\r\n')
            elif upper.startswith('RSET'):
                mail_from, rcpt_to, data_lines = None, [], []
                self._send(b'250 OK\r\n')
            else:
                self._send(b'500 unrecognized command\r\n')


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeSMTPServer:
    """Usage:

        with FakeSMTPServer() as server:
            send_email(..., config=SmtpConfig(host=server.host, port=server.port, ..., use_tls=False))
            assert len(server.received) == 1

    With TLS + AUTH (see make_self_signed_contexts):

        server_ctx, client_ctx = make_self_signed_contexts(tmp_path)
        with FakeSMTPServer(tls_context=server_ctx, credentials=('u', 'p'), require_tls=True) as server:
            ...
            assert server.starttls_count == 1 and server.auth_attempts == [('u', 'p', True)]
    """

    def __init__(self, host: str = '127.0.0.1', *, tls_context=None, credentials=None, require_tls: bool = False):
        self._server = _Server((host, 0), _SMTPHandler)
        self._server.received = []
        self._server.tls_context = tls_context
        self._server.credentials = tuple(credentials) if credentials is not None else None
        self._server.require_tls = require_tls
        self._server.starttls_count = 0
        self._server.auth_attempts = []
        self._thread = None

    @property
    def host(self) -> str:
        return self._server.server_address[0]

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def received(self) -> list:
        return self._server.received

    @property
    def starttls_count(self) -> int:
        return self._server.starttls_count

    @property
    def auth_attempts(self) -> list:
        return self._server.auth_attempts

    def __enter__(self) -> 'FakeSMTPServer':
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={'poll_interval': 0.05})
        self._thread.daemon = True
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2)


def make_self_signed_contexts(directory) -> tuple:
    """(server_context, client_context): a throwaway self-signed certificate for
    127.0.0.1 generated with `cryptography`, and a client context that trusts
    ONLY that certificate. Hostname checking stays ON in the client context, so
    a sender that verifies the server properly is what the test exercises."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    directory = Path(directory)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake.smtp.test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.ip_address('127.0.0.1')), x509.DNSName('localhost'),
        ]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / 'smtp-test-cert.pem', directory / 'smtp-test-key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(cert_path), str(key_path))
    client_ctx = ssl.create_default_context(cafile=str(cert_path))
    return server_ctx, client_ctx
