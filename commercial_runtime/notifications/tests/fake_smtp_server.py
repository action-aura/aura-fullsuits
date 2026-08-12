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
client behavior to complete a real send. STARTTLS/AUTH are deliberately
NOT implemented -- every test using this fixture sets AURA_SMTP_USE_TLS=0
and leaves AURA_SMTP_USER unset, so smtp_client.py never attempts either,
matching how a real local debug relay (e.g. MailHog, Mailpit) is normally
run in plaintext-only mode for exactly this kind of test.
"""
from __future__ import annotations

import socketserver
import threading


class _SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b'220 fake.smtp.test ESMTP\r\n')
        mail_from = None
        rcpt_to = []
        data_lines = []
        in_data = False

        while True:
            line = self.rfile.readline()
            if not line:
                break

            if in_data:
                if line.rstrip(b'\r\n') == b'.':
                    in_data = False
                    self.server.received.append({
                        'mail_from': mail_from,
                        'rcpt_to': list(rcpt_to),
                        'data': b''.join(data_lines).decode('utf-8', errors='replace'),
                    })
                    mail_from, rcpt_to, data_lines = None, [], []
                    self.wfile.write(b'250 OK: queued\r\n')
                else:
                    # RFC 5321 dot-stuffing: a leading '.' on a real content
                    # line is escaped as '..' by the client; undo that here.
                    if line.startswith(b'..'):
                        line = line[1:]
                    data_lines.append(line)
                continue

            cmd = line.decode('utf-8', errors='replace').strip()
            upper = cmd.upper()
            if upper.startswith('EHLO') or upper.startswith('HELO'):
                self.wfile.write(b'250 fake.smtp.test\r\n')
            elif upper.startswith('MAIL FROM'):
                mail_from = cmd
                self.wfile.write(b'250 OK\r\n')
            elif upper.startswith('RCPT TO'):
                rcpt_to.append(cmd)
                self.wfile.write(b'250 OK\r\n')
            elif upper.startswith('DATA'):
                in_data = True
                self.wfile.write(b'354 End data with <CR><LF>.<CR><LF>\r\n')
            elif upper.startswith('QUIT'):
                self.wfile.write(b'221 Bye\r\n')
                break
            elif upper.startswith('NOOP'):
                self.wfile.write(b'250 OK\r\n')
            elif upper.startswith('RSET'):
                mail_from, rcpt_to, data_lines = None, [], []
                self.wfile.write(b'250 OK\r\n')
            else:
                self.wfile.write(b'500 unrecognized command\r\n')


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeSMTPServer:
    """Usage:

        with FakeSMTPServer() as server:
            send_email(..., config=SmtpConfig(host=server.host, port=server.port, ..., use_tls=False))
            assert len(server.received) == 1
    """

    def __init__(self, host: str = '127.0.0.1'):
        self._server = _Server((host, 0), _SMTPHandler)
        self._server.received = []
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
