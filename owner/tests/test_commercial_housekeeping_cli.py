"""`flask commercial housekeeping` (2026-10-10): quote expiry and the nonce /
idempotency / rate-limit purges had functions and no caller. With --apply each
existing function must run exactly once; without it, none of the deleting
paths may run (only counts are reported)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.commercial_sales import quotes
from app.licensing_service import idempotency, ratelimit, replay


@pytest.fixture()
def calls(monkeypatch):
    seen: list[str] = []

    def _stub(name, value):
        def _fn(*args, **kwargs):
            seen.append(name)
            return value
        return _fn

    monkeypatch.setattr(quotes, "expire_stale_quotes", _stub("quotes", 1))
    monkeypatch.setattr(replay, "purge_expired_nonces", _stub("nonces", 2))
    monkeypatch.setattr(idempotency, "purge_expired", _stub("idempotency", 3))
    monkeypatch.setattr(ratelimit, "purge_old_counters", _stub("ratelimit", 4))
    return seen


def _run(app, *args):
    return app.test_cli_runner().invoke(args=["commercial", "housekeeping", *args])


def test_apply_calls_each_function_exactly_once(app, calls):
    result = _run(app, "--apply")
    assert result.exit_code == 0, result.output
    assert sorted(calls) == ["idempotency", "nonces", "quotes", "ratelimit"]
    assert json.loads(result.output) == {
        "dry_run": False, "quotes_expired": 1, "nonces_purged": 2,
        "idempotency_records_purged": 3, "rate_limit_counters_purged": 4,
        # SEC-09: real (unstubbed) privacy rules over an empty database.
        "email_bodies_cleared": 0, "login_attempts_purged": 0,
    }


def test_dry_run_calls_no_deleting_path_and_reports_counts(app, calls):
    result = _run(app)
    assert result.exit_code == 0, result.output
    assert calls == []
    report = json.loads(result.output)
    assert report["dry_run"] is True
    assert set(report) == {"dry_run", "quotes_expired", "nonces_purged", "idempotency_records_purged", "rate_limit_counters_purged",
                         "email_bodies_cleared", "login_attempts_purged"}


def test_dry_run_counts_are_real_and_nothing_is_deleted(app):
    """Unstubbed: an expired nonce is counted, and still there afterwards."""
    from app.extensions import db_session
    from app.models.licensing_service import SecurityNonceRecord

    with app.app_context():
        db_session.add(SecurityNonceRecord(nonce="n-expired", scope="t", expires_at=datetime.now(timezone.utc) - timedelta(hours=1)))
        db_session.commit()
    result = _run(app)
    assert json.loads(result.output)["nonces_purged"] == 1
    with app.app_context():
        assert db_session.query(SecurityNonceRecord).count() == 1
    assert json.loads(_run(app, "--apply").output)["nonces_purged"] == 1
    with app.app_context():
        assert db_session.query(SecurityNonceRecord).count() == 0
