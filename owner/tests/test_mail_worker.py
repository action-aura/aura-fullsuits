"""Owner's mail worker, SMTP transport, invitation mail and `flask email send-test`.

The SMTP side runs against a REAL socket server that implements STARTTLS and
AUTH PLAIN with a self-signed certificate generated in the test
(commercial_runtime/notifications/tests/fake_smtp_server.py). It refuses MAIL
before STARTTLS and before AUTH, and the assertions read what the SERVER saw
(upgrade count, the login it received, TLS on the delivered message): that is
what proves starttls() and login() are actually called with the configured
user -- a worker that "sent" over a plaintext, unauthenticated connection would
be refused by the server and leave the row unsent, instead of passing.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from commercial_runtime.notifications.tests.fake_smtp_server import FakeSMTPServer, make_self_signed_contexts  # noqa: E402

from app.extensions import db_session  # noqa: E402
from app.mail import cli as mail_cli  # noqa: E402
from app.mail import outbox  # noqa: E402
from app.mail import worker as mail_worker  # noqa: E402
from app.models.email_outbox import EmailOutbox  # noqa: E402
from tests.conftest import get_csrf, login_and_verify_mfa, make_staff  # noqa: E402

USER = "owner-mailer@actionaura.test"
PASSWORD = "fake-owner-smtp-password-1"


def _configure(app, server, **over):
    app.config.update(SMTP_HOST=server.host, SMTP_PORT=str(server.port), SMTP_USER=USER, SMTP_PASSWORD=PASSWORD,
                      SMTP_FROM="noreply@actionaura.test", SMTP_FROM_NAME="Action Aura", SMTP_USE_TLS=True)
    app.config.update(over)


def _queue(app, to="person@shop.example", kind="staff_invitation", subject="Hello"):
    with app.app_context():
        row = outbox.enqueue(kind=kind, to_address=to, subject=subject, body_text="Body text", body_html="<p>Body</p>")
        db_session.commit()
        return row.id


def _row(app, row_id):
    with app.app_context():
        db_session.expire_all()
        r = db_session.get(EmailOutbox, row_id)
        return {c.name: getattr(r, c.name) for c in EmailOutbox.__table__.columns}


def _worker(app, ctx):
    return mail_worker.EmailWorker(app, ssl_context=ctx, pacing_seconds=0)


@pytest.fixture
def tls(tmp_path):
    return make_self_signed_contexts(tmp_path)


# ---------------------------------------------------------------------------
# worker + transport
# ---------------------------------------------------------------------------
def test_worker_really_does_starttls_and_authenticates_with_the_configured_user(app, tls):
    server_ctx, client_ctx = tls
    with FakeSMTPServer(tls_context=server_ctx, credentials=(USER, PASSWORD), require_tls=True) as server:
        _configure(app, server)
        row_id = _queue(app)
        result = _worker(app, client_ctx).run_once()
    assert result["outcomes"]["sent"] == 1
    assert server.starttls_count == 1
    assert server.auth_attempts == [(USER, PASSWORD, True)]
    assert len(server.received) == 1
    got = server.received[0]
    assert got["tls"] is True and got["auth_user"] == USER
    assert "<person@shop.example>" in got["rcpt_to"][0] and "noreply@actionaura.test" in got["mail_from"]
    assert "Subject: Hello" in got["data"] and "Reply-To: support@actionaura.me" in got["data"]
    assert "Message-ID: <" in got["data"] and "Date: " in got["data"]
    row = _row(app, row_id)
    assert row["status"] == "SENT" and row["sent_at"] is not None and row["last_error"] is None


def test_wrong_password_is_a_retry_with_the_class_name_only_and_nothing_is_delivered(app, tls):
    server_ctx, client_ctx = tls
    with FakeSMTPServer(tls_context=server_ctx, credentials=(USER, PASSWORD), require_tls=True) as server:
        _configure(app, server, SMTP_PASSWORD="wrong-password-xyz")
        row_id = _queue(app)
        result = _worker(app, client_ctx).run_once()
    assert result["outcomes"] == {"sent": 0, "retry": 1, "failed_permanent": 0}
    assert server.received == []
    row = _row(app, row_id)
    assert row["status"] == "QUEUED" and row["attempt_count"] == 1
    assert row["last_error"] == "SMTPAuthenticationError"
    assert "wrong-password-xyz" not in str(row) and PASSWORD not in str(row)
    assert row["next_attempt_at"] > datetime.now(timezone.utc)


def test_without_starttls_the_tls_only_server_refuses_and_the_row_is_not_sent(app, tls):
    server_ctx, client_ctx = tls
    with FakeSMTPServer(tls_context=server_ctx, credentials=(USER, PASSWORD), require_tls=True) as server:
        _configure(app, server, SMTP_USE_TLS=False)
        row_id = _queue(app)
        _worker(app, client_ctx).run_once()
    assert server.starttls_count == 0 and server.received == []
    assert _row(app, row_id)["status"] == "QUEUED"


def test_an_untrusted_certificate_is_refused_by_default(app, tls):
    """No ssl_context passed == the default VERIFYING context, which does not
    trust the test's self-signed certificate."""
    server_ctx, _client_ctx = tls
    with FakeSMTPServer(tls_context=server_ctx, credentials=(USER, PASSWORD), require_tls=True) as server:
        _configure(app, server)
        row_id = _queue(app)
        _worker(app, None).run_once()
    assert server.received == [] and server.auth_attempts == []
    row = _row(app, row_id)
    assert row["status"] == "QUEUED" and row["last_error"] == "SSLCertVerificationError"


def test_the_eighth_failed_attempt_is_permanent(app, tls):
    server_ctx, client_ctx = tls
    with FakeSMTPServer(tls_context=server_ctx, credentials=(USER, PASSWORD), require_tls=True) as server:
        _configure(app, server, SMTP_PASSWORD="nope")
        row_id = _queue(app)
        with app.app_context():
            db_session.get(EmailOutbox, row_id).attempt_count = mail_worker.MAX_ATTEMPTS - 1
            db_session.commit()
        result = _worker(app, client_ctx).run_once()
    assert result["outcomes"]["failed_permanent"] == 1
    assert _row(app, row_id)["status"] == "FAILED_PERMANENT"


def test_smtp_off_rows_stay_queued_and_the_worker_does_nothing(app):
    app.config.update(SMTP_HOST="")
    row_id = _queue(app)
    assert _worker(app, None).run_once() == {"ran": False, "reason": "smtp_not_configured"}
    assert _row(app, row_id)["status"] == "QUEUED" and _row(app, row_id)["attempt_count"] == 0


def test_the_worker_thread_is_started_only_when_smtp_is_set_and_not_testing(app, monkeypatch):
    started = []
    monkeypatch.setattr(mail_worker.EmailWorker, "start", lambda self, *a, **k: started.append(1))
    # SMTP off, not a test app -> never started
    app.config.update(SMTP_HOST="", TESTING=False)
    assert mail_worker.start_worker_if_configured(app) is None and started == []
    # SMTP on but a TEST app -> never started
    app.config.update(SMTP_HOST="smtp.example", TESTING=True)
    assert mail_worker.start_worker_if_configured(app) is None and started == []
    # SMTP on and a real app -> started exactly once (positive control)
    app.config.update(SMTP_HOST="smtp.example", TESTING=False)
    assert mail_worker.start_worker_if_configured(app) is not None
    assert started == [1]
    app.config.update(TESTING=True)


def test_the_create_app_factory_starts_no_worker_for_a_test_app(app):
    assert "owner_mail_worker" not in app.extensions


def test_a_claimed_row_cannot_be_claimed_twice_and_an_expired_lease_is_reclaimed(app):
    row_id = _queue(app)
    with app.app_context():
        first = outbox.claim_due(batch_size=10, lease_seconds=60)
        second = outbox.claim_due(batch_size=10, lease_seconds=60)
        assert first == [row_id] and second == []
        db_session.get(EmailOutbox, row_id).lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()
        assert outbox.reclaim_expired_leases() == 1
        db_session.expire_all()
        assert db_session.get(EmailOutbox, row_id).status == "QUEUED"


def test_a_line_break_in_the_recipient_or_subject_cannot_be_queued(app):
    with app.app_context():
        for to, subject in (("a@b.example\nBcc: x@y.example", "s"), ("a@b.example", "s\r\nBcc: x@y.example")):
            with pytest.raises(ValueError):
                outbox.enqueue(kind="test", to_address=to, subject=subject, body_text="b")
        assert db_session.execute(select(EmailOutbox)).first() is None


# ---------------------------------------------------------------------------
# invitation mail
# ---------------------------------------------------------------------------
def _invite(app, client, email="newbie@example.com"):
    make_staff(app, "boss@example.com", super_admin=True, mfa=True)
    login_and_verify_mfa(client, "boss@example.com")
    csrf = get_csrf(client.get("/staff").get_data(as_text=True))
    return client.post("/staff/invite", data={"csrf_token": csrf, "email": email, "role_codes": ["SALES"]})


def _all_rows(app):
    with app.app_context():
        return db_session.execute(select(EmailOutbox)).scalars().all()


def test_an_invitation_queues_one_email_with_the_link_when_smtp_is_on(app, client, seeded):
    app.config.update(SMTP_HOST="smtp.example")
    resp = _invite(app, client)
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    link = re.search(r"(http[^\s<\"]*/auth/accept-invitation/[^\s<\"]+)", page).group(1)
    rows = _all_rows(app)
    assert len(rows) == 1
    row = rows[0]
    assert (row.kind, row.source, row.to_address, row.status) == ("staff_invitation", "owner", "newbie@example.com", "QUEUED")
    assert link in row.body_text and link in row.body_html
    assert "We emailed this link to newbie@example.com" in page      # the page says it was sent...
    assert link in page                                              # ...and still shows the link once


def test_with_smtp_off_no_email_is_queued_and_the_page_keeps_the_copy_link_text(app, client, seeded):
    app.config.update(SMTP_HOST="")
    resp = _invite(app, client)
    page = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert _all_rows(app) == []
    assert "Copy this one-time link" in page and "We emailed this link" not in page
    assert "/auth/accept-invitation/" in page


# ---------------------------------------------------------------------------
# flask email send-test
# ---------------------------------------------------------------------------
def test_send_test_cli_prints_the_smtp_result_line_against_the_fake_server(app, tls, monkeypatch):
    server_ctx, client_ctx = tls
    monkeypatch.setattr(mail_cli, "SSL_CONTEXT", client_ctx)
    with FakeSMTPServer(tls_context=server_ctx, credentials=(USER, PASSWORD), require_tls=True) as server:
        _configure(app, server)
        result = app.test_cli_runner().invoke(args=["email", "send-test", "--to", "me@example.com"])
    assert result.exit_code == 0, result.output
    assert "SMTP result: 250 OK: queued" in result.output
    assert PASSWORD not in result.output
    assert len(server.received) == 1 and server.starttls_count == 1
    assert "Aura Owner test message" in server.received[0]["data"]


def test_send_test_cli_says_so_plainly_when_email_is_off_or_the_send_fails(app, tls, monkeypatch):
    app.config.update(SMTP_HOST="")
    off = app.test_cli_runner().invoke(args=["email", "send-test", "--to", "me@example.com"])
    assert off.exit_code != 0 and "not set up" in off.output

    server_ctx, client_ctx = tls
    monkeypatch.setattr(mail_cli, "SSL_CONTEXT", client_ctx)
    with FakeSMTPServer(tls_context=server_ctx, credentials=(USER, PASSWORD), require_tls=True) as server:
        _configure(app, server, SMTP_PASSWORD="bad-pass-zzz")
        bad = app.test_cli_runner().invoke(args=["email", "send-test", "--to", "me@example.com"])
    assert bad.exit_code != 0 and "SMTPAuthenticationError" in bad.output
    assert "bad-pass-zzz" not in bad.output and server.received == []
