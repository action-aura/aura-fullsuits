"""Retention for customer website accounts (app/privacy_retention.py): login
attempts 90 days; sessions and tokens 30 days AFTER they expired; link-bearing
mail bodies cleared once finished. Each rule is shown deleting the old row AND
keeping the live one."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from tests.test_customer_accounts_service import GOOD, IP, active_account, setup_accounts

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def _count(model):
    from app.extensions import db_session

    return db_session.execute(select(func.count()).select_from(model)).scalar_one()


def test_customer_login_attempts_older_than_90_days_go_and_newer_stay(app):
    from app import privacy_retention as pr
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerLoginAttempt

    with app.app_context():
        for days in (91, 89):
            db_session.add(CustomerLoginAttempt(email_hash="e" * 64, ip_hash="i" * 64, success=False,
                                                reason="UNKNOWN", created_at=NOW - timedelta(days=days)))
        db_session.commit()
        assert pr.count_customer_login_attempts_to_purge(now=NOW) == 1  # the dry run counts what the real run deletes
        assert pr.purge_customer_login_attempts(now=NOW) == 1
        assert _count(CustomerLoginAttempt) == 1
        remaining = db_session.execute(select(CustomerLoginAttempt)).scalars().one()
        assert remaining.created_at == NOW - timedelta(days=89)


def test_staff_login_attempts_are_untouched_by_the_customer_purge(app):
    from app import privacy_retention as pr
    from app.extensions import db_session
    from app.models.staff import LoginAttempt

    with app.app_context():
        db_session.add(LoginAttempt(email_attempted="boss@example.com", success=False, created_at=NOW - timedelta(days=400)))
        db_session.commit()
        pr.purge_customer_login_attempts(now=NOW)
        assert _count(LoginAttempt) == 1


def test_sessions_and_tokens_go_30_days_after_they_expired_never_before(app):
    from app import privacy_retention as pr
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerAccount, CustomerAccountToken, CustomerSession

    setup_accounts(app)
    with app.app_context():
        active_account(app)
        account = db_session.execute(select(CustomerAccount)).scalars().one()
        for i in range(2):
            service.sign_in("owner@haddad.example", GOOD, f"198.51.100.{i + 1}", None)
        service.issue_reset_link(account, None)
        sessions = db_session.execute(select(CustomerSession).order_by(CustomerSession.created_at)).scalars().all()
        tokens = db_session.execute(select(CustomerAccountToken)).scalars().all()
        assert len(sessions) == 2 and len(tokens) == 2  # invite (spent) + reset (open)
        # one session expired 31 days ago, one 29 days ago
        sessions[0].absolute_expires_at = NOW - timedelta(days=31)
        sessions[1].absolute_expires_at = NOW - timedelta(days=29)
        old_token = next(t for t in tokens if t.kind == "INVITE")
        old_token.expires_at = NOW - timedelta(days=31)
        live_token = next(t for t in tokens if t.kind == "RESET")
        live_token.expires_at = NOW + timedelta(minutes=20)
        db_session.commit()
        assert pr.count_expired_customer_sessions_and_tokens(now=NOW) == 2
        assert pr.purge_expired_customer_sessions_and_tokens(now=NOW) == 2
        assert [s.id for s in db_session.execute(select(CustomerSession)).scalars().all()] == [sessions[1].id]
        assert [t.id for t in db_session.execute(select(CustomerAccountToken)).scalars().all()] == [live_token.id]


def test_finished_link_mail_loses_its_body_but_queued_and_other_mail_keep_theirs(app):
    from app import privacy_retention as pr
    from app.extensions import db_session
    from app.mail import outbox
    from app.models.email_outbox import EmailOutbox

    with app.app_context():
        rows = {}
        for key, kind, status in (("sent_link", "customer_invitation", "SENT"), ("failed_link", "customer_password_reset", "FAILED_PERMANENT"),
                                  ("queued_link", "customer_invitation", "QUEUED"), ("sent_other", "staff_invitation", "SENT"),
                                  ("notice", "customer_password_changed", "SENT")):
            row = outbox.enqueue(kind=kind, to_address="a@b.example", subject="s", body_text="https://x/#t=SECRET", body_html="<a>t</a>")
            row.status = status
            rows[key] = row
        db_session.commit()
        assert pr.count_customer_link_mail_bodies_to_clear() == 2
        assert pr.clear_customer_link_mail_bodies() == 2
        db_session.expire_all()
        body = {key: db_session.get(EmailOutbox, row.id).body_text for key, row in rows.items()}
        assert body == {"sent_link": "", "failed_link": "", "queued_link": "https://x/#t=SECRET",
                        "sent_other": "https://x/#t=SECRET", "notice": "https://x/#t=SECRET"}
        assert pr.clear_customer_link_mail_bodies() == 0  # idempotent


def test_the_housekeeping_command_reports_the_new_counters(app):
    import json

    result = app.test_cli_runner().invoke(args=["commercial", "housekeeping"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert {"customer_login_attempts_purged", "customer_sessions_and_tokens_purged", "customer_link_mail_bodies_cleared"} <= set(data)
