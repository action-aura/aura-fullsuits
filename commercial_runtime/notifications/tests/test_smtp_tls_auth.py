"""The product's smtp_client over a server that really implements STARTTLS and
AUTH -- the path memory flagged as having ZERO coverage (every older test ran
with AURA_SMTP_USE_TLS=0 and no user, so starttls() and login() were never
exercised at all).

The fake server refuses MAIL before STARTTLS (530) and before AUTH (530), so a
sender that skipped either step fails loudly instead of "succeeding"; and the
assertions read what the SERVER saw (TLS upgrade count, the login it received,
whether the message arrived over TLS), not what the client claims it did.
"""
import pytest

from commercial_runtime.notifications.smtp_client import SmtpConfig, send_email
from commercial_runtime.notifications.tests.fake_smtp_server import FakeSMTPServer, make_self_signed_contexts


def _cfg(server, **over):
    base = dict(host=server.host, port=server.port, username='shop-user', password='pw-1',
                from_address='shop@x.test', use_tls=True)
    base.update(over)
    return SmtpConfig(**base)


def test_starttls_and_login_are_really_performed(tmp_path):
    server_ctx, _client_ctx = make_self_signed_contexts(tmp_path)
    with FakeSMTPServer(tls_context=server_ctx, credentials=('shop-user', 'pw-1'), require_tls=True) as server:
        send_email(recipient='to@x.test', subject='Hi', body_text='body', config=_cfg(server))
    assert server.starttls_count == 1
    assert server.auth_attempts == [('shop-user', 'pw-1', True)]
    assert len(server.received) == 1
    assert server.received[0]['tls'] is True and server.received[0]['auth_user'] == 'shop-user'


def test_wrong_password_is_refused_and_nothing_is_delivered(tmp_path):
    import smtplib
    server_ctx, _ = make_self_signed_contexts(tmp_path)
    with FakeSMTPServer(tls_context=server_ctx, credentials=('shop-user', 'pw-1'), require_tls=True) as server:
        with pytest.raises(smtplib.SMTPAuthenticationError):
            send_email(recipient='to@x.test', subject='Hi', body_text='body', config=_cfg(server, password='wrong'))
    assert server.received == []
    assert server.auth_attempts == [('shop-user', 'wrong', False)]


def test_use_tls_off_against_a_tls_only_server_fails_instead_of_sending_plaintext(tmp_path):
    import smtplib
    server_ctx, _ = make_self_signed_contexts(tmp_path)
    with FakeSMTPServer(tls_context=server_ctx, credentials=('shop-user', 'pw-1'), require_tls=True) as server:
        with pytest.raises(smtplib.SMTPException):
            send_email(recipient='to@x.test', subject='Hi', body_text='body', config=_cfg(server, use_tls=False))
    assert server.received == [] and server.starttls_count == 0
