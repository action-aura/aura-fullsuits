import email as email_lib

import pytest

from commercial_runtime.notifications import smtp_client
from commercial_runtime.notifications.tests.fake_smtp_server import FakeSMTPServer


def _decoded_text_parts(raw_data: str) -> list[str]:
    """The fake server captures the exact wire bytes smtplib sent, which
    email.mime.text.MIMEText base64-encodes by default for a utf-8 payload
    -- parse it back with the stdlib email parser rather than substring-
    matching the raw (encoded) wire form."""
    msg = email_lib.message_from_string(raw_data)
    parts = [msg] if not msg.is_multipart() else msg.get_payload()
    return [p.get_payload(decode=True).decode('utf-8') for p in parts]


def test_load_config_from_env_returns_none_when_host_unset(monkeypatch):
    monkeypatch.delenv('AURA_SMTP_HOST', raising=False)
    assert smtp_client.load_config_from_env() is None
    assert smtp_client.is_configured() is False


def test_load_config_from_env_returns_none_when_host_is_blank(monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', '   ')
    assert smtp_client.load_config_from_env() is None


def test_load_config_from_env_reads_all_fields(monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('AURA_SMTP_PORT', '2525')
    monkeypatch.setenv('AURA_SMTP_USER', 'bot@example.com')
    monkeypatch.setenv('AURA_SMTP_PASSWORD', 'secret')
    monkeypatch.setenv('AURA_SMTP_FROM_ADDRESS', 'noreply@example.com')
    monkeypatch.setenv('AURA_SMTP_USE_TLS', '0')

    cfg = smtp_client.load_config_from_env()
    assert cfg.host == 'smtp.example.com'
    assert cfg.port == 2525
    assert cfg.username == 'bot@example.com'
    assert cfg.password == 'secret'
    assert cfg.from_address == 'noreply@example.com'
    assert cfg.use_tls is False
    assert smtp_client.is_configured() is True


def test_from_address_falls_back_to_username_when_unset(monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('AURA_SMTP_USER', 'bot@example.com')
    monkeypatch.delenv('AURA_SMTP_FROM_ADDRESS', raising=False)
    cfg = smtp_client.load_config_from_env()
    assert cfg.from_address == 'bot@example.com'


def test_malformed_port_falls_back_to_default(monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('AURA_SMTP_PORT', 'not-a-number')
    cfg = smtp_client.load_config_from_env()
    assert cfg.port == 587


def test_send_email_raises_not_configured_when_no_host(monkeypatch):
    monkeypatch.delenv('AURA_SMTP_HOST', raising=False)
    with pytest.raises(smtp_client.SmtpNotConfiguredError):
        smtp_client.send_email(recipient='a@b.com', subject='s', body_text='hi')


def test_send_email_against_a_real_socket_delivers_the_message():
    """The one true end-to-end proof this module's transport actually
    works: a real TCP connection, real SMTP protocol exchange, against a
    real (if minimal) server -- not a mocked smtplib.SMTP."""
    with FakeSMTPServer() as server:
        cfg = smtp_client.SmtpConfig(
            host=server.host, port=server.port, username='', password='',
            from_address='sender@aura.test', use_tls=False,
        )
        smtp_client.send_email(
            recipient='owner@company.test', subject='Low stock: Widget',
            body_text='Stock is at 3 units.', config=cfg,
        )

        assert len(server.received) == 1
        received = server.received[0]
        assert 'sender@aura.test' in received['mail_from']
        assert any('owner@company.test' in r for r in received['rcpt_to'])
        assert 'Low stock: Widget' in received['data']  # header, not base64-encoded
        assert 'Stock is at 3 units.' in _decoded_text_parts(received['data'])[0]


def test_send_email_html_variant_includes_both_parts():
    with FakeSMTPServer() as server:
        cfg = smtp_client.SmtpConfig(
            host=server.host, port=server.port, username='', password='',
            from_address='sender@aura.test', use_tls=False,
        )
        smtp_client.send_email(
            recipient='owner@company.test', subject='Report', body_text='plain body',
            body_html='<p>html body</p>', config=cfg,
        )
        assert len(server.received) == 1
        parts = _decoded_text_parts(server.received[0]['data'])
        assert any('plain body' in p for p in parts)
        assert any('html body' in p for p in parts)


def test_send_email_raises_when_server_unreachable():
    """No fake server listening on this port -- send_email must raise
    (OSError/ConnectionRefusedError via smtplib), never silently succeed or
    hang. worker.py relies on exactly this behavior to translate a send
    failure into a retry."""
    cfg = smtp_client.SmtpConfig(
        host='127.0.0.1', port=1, username='', password='',
        from_address='sender@aura.test', use_tls=False,
    )
    with pytest.raises(Exception):
        smtp_client.send_email(recipient='x@y.com', subject='s', body_text='b',
                                config=cfg, timeout_seconds=2.0)
