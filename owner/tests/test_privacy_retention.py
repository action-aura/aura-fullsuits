"""SEC-09 retention: finished e-mail rows lose bodies/recipient/subject after N
days, login telemetry is deleted after N days. Every rule is tested BOTH ways
(old-and-finished goes; recent or unsent stays) -- a rule that only had the
"goes" half would pass if it wiped everything."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import privacy_retention as pr
from app.extensions import db_session
from app.models.email_outbox import EmailOutbox
from app.models.staff import LoginAttempt

NOW = datetime.now(timezone.utc)


def _mail(status, age_days, *, sent=True, to="Owner.Person@Shop.example"):
    when = NOW - timedelta(days=age_days)
    row = EmailOutbox(
        id=uuid.uuid4(), kind="receipt", to_address=to, subject="Receipt for Ahmad", body_text="secret body",
        body_html="<p>secret</p>", status=status, attempt_count=1, source="relay", created_at=when,
        sent_at=when if (sent and status == "SENT") else None,
    )
    db_session.add(row)
    db_session.commit()
    return row.id


def _attempt(age_days, email="a@b.example"):
    row = LoginAttempt(email_attempted=email, ip_address="203.0.113.9", success=False, reason="bad_password")
    row.created_at = NOW - timedelta(days=age_days)
    db_session.add(row)
    db_session.commit()
    return row.id


@pytest.fixture()
def ctx(app):
    with app.app_context():
        yield


def _row(row_id):
    db_session.expire_all()
    return db_session.get(EmailOutbox, row_id)


def test_old_sent_mail_is_cleared_but_keeps_audit_metadata(ctx):
    rid = _mail("SENT", 40)
    assert pr.clear_old_email_bodies(now=NOW) == 1
    row = _row(rid)
    assert (row.body_text, row.body_html, row.subject) == ("", None, pr.REDACTED_SUBJECT)
    assert row.to_address == "***@Shop.example"
    assert (row.kind, row.status, row.source, row.attempt_count) == ("receipt", "SENT", "relay", 1)
    assert row.sent_at is not None and row.created_at is not None


@pytest.mark.parametrize("status", ["FAILED_PERMANENT", "CANCELLED"])
def test_old_permanently_finished_mail_is_cleared(ctx, status):
    rid = _mail(status, 40)
    assert pr.clear_old_email_bodies(now=NOW) == 1
    assert _row(rid).body_text == ""


def test_recent_sent_mail_is_kept(ctx):
    rid = _mail("SENT", 5)
    assert pr.clear_old_email_bodies(now=NOW) == 0
    assert _row(rid).body_text == "secret body"


@pytest.mark.parametrize("status", ["QUEUED", "SENDING"])
def test_unsent_mail_is_kept_however_old(ctx, status):
    rid = _mail(status, 400)
    assert pr.clear_old_email_bodies(now=NOW) == 0
    row = _row(rid)
    assert row.body_text == "secret body" and row.to_address == "Owner.Person@Shop.example"


def test_second_run_is_a_noop_and_counts_match_apply(ctx):
    _mail("SENT", 40)
    _mail("SENT", 41)
    assert pr.count_email_bodies_to_clear(now=NOW) == 2
    assert pr.clear_old_email_bodies(now=NOW) == 2
    assert pr.count_email_bodies_to_clear(now=NOW) == 0
    assert pr.clear_old_email_bodies(now=NOW) == 0


def test_window_boundary(ctx):
    inside = _mail("SENT", 29)
    outside = _mail("SENT", 31)
    pr.clear_old_email_bodies(now=NOW)
    assert _row(inside).body_text == "secret body"
    assert _row(outside).body_text == ""


def test_env_window_is_honoured_and_floored(ctx, monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL_BODY_RETENTION_DAYS", "10")
    assert pr.email_body_days() == 10
    monkeypatch.setenv("OWNER_EMAIL_BODY_RETENTION_DAYS", "0")
    assert pr.email_body_days() == pr.MIN_EMAIL_DAYS  # never below the relay caps' look-back
    monkeypatch.setenv("OWNER_EMAIL_BODY_RETENTION_DAYS", "junk")
    assert pr.email_body_days() == pr.DEFAULT_EMAIL_BODY_DAYS
    monkeypatch.setenv("OWNER_EMAIL_BODY_RETENTION_DAYS", "10")
    old, fresh = _mail("SENT", 11), _mail("SENT", 9)
    pr.clear_old_email_bodies(now=NOW)
    assert _row(old).body_text == "" and _row(fresh).body_text == "secret body"


def test_login_attempts_old_deleted_recent_kept(ctx):
    old, recent = _attempt(200), _attempt(10)
    assert pr.count_login_attempts_to_purge(now=NOW) == 1
    assert pr.purge_old_login_attempts(now=NOW) == 1
    db_session.expire_all()
    assert db_session.get(LoginAttempt, old) is None
    assert db_session.get(LoginAttempt, recent) is not None


def test_login_attempt_boundary(ctx):
    inside, outside = _attempt(179), _attempt(181)
    pr.purge_old_login_attempts(now=NOW)
    db_session.expire_all()
    assert db_session.get(LoginAttempt, inside) is not None
    assert db_session.get(LoginAttempt, outside) is None


def test_housekeeping_cli_runs_both_rules_end_to_end(app):
    with app.app_context():
        mail_id = _mail("SENT", 60)
        attempt_id = _attempt(300)
    runner = app.test_cli_runner()
    dry = json.loads(runner.invoke(args=["commercial", "housekeeping"]).output)
    assert (dry["email_bodies_cleared"], dry["login_attempts_purged"]) == (1, 1)
    with app.app_context():
        assert _row(mail_id).body_text == "secret body"  # dry run touched nothing
    done = json.loads(runner.invoke(args=["commercial", "housekeeping", "--apply"]).output)
    assert (done["email_bodies_cleared"], done["login_attempts_purged"]) == (1, 1)
    with app.app_context():
        assert _row(mail_id).body_text == ""
        assert db_session.get(LoginAttempt, attempt_id) is None
