import email as email_lib
import sqlite3

import pytest

from commercial_runtime.notifications import settings
from commercial_runtime.notifications.outbox import EmailOutboxRepository
from commercial_runtime.notifications.schema import apply_notifications_schema
from commercial_runtime.notifications.tests.fake_smtp_server import FakeSMTPServer
from commercial_runtime.notifications.worker import EmailOutboxWorker, compute_backoff_delay


def _decoded_body(raw_data: str) -> str:
    msg = email_lib.message_from_string(raw_data)
    return msg.get_payload(decode=True).decode('utf-8')


def _conn_factory(db_path):
    def _make():
        c = sqlite3.connect(db_path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        return c
    return _make


def _setup_db(tmp_path, company_id=1, enabled=True):
    db_path = str(tmp_path / 'product.db')
    conn = sqlite3.connect(db_path)
    apply_notifications_schema(conn)
    if enabled:
        settings.set_setting(conn, company_id, 'enabled', '1')
    conn.commit()
    conn.close()
    return db_path


def _enqueue_directly(db_path, **overrides):
    conn = sqlite3.connect(db_path)
    kwargs = dict(company_id=1, email_type='low_stock_alert', recipient='owner@shop.test',
                  subject='Low stock: Widget', body_text='Stock is at 3 units.')
    kwargs.update(overrides)
    row_id = EmailOutboxRepository(conn).enqueue(**kwargs)
    conn.commit()
    conn.close()
    return row_id


def _make_worker(db_path, **overrides):
    kwargs = dict(conn_factory=_conn_factory(db_path), company_id=1, rate_limit_seconds=0)
    kwargs.update(overrides)
    return EmailOutboxWorker(**kwargs)


# ── inertness when unconfigured ──────────────────────────────────────────

def test_run_once_does_nothing_when_smtp_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv('AURA_SMTP_HOST', raising=False)
    db_path = _setup_db(tmp_path)  # company opted in, but SMTP still unconfigured
    _enqueue_directly(db_path)

    worker = _make_worker(db_path)
    result = worker.run_once()

    assert result == {'ran': False, 'reason': 'disabled'}
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT status FROM email_outbox").fetchone()[0] == 'QUEUED', \
        "an unconfigured install must never move a queued row toward SENDING"


def test_run_once_does_nothing_when_company_not_opted_in(tmp_path, monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.com')
    db_path = _setup_db(tmp_path, enabled=False)
    _enqueue_directly(db_path)

    worker = _make_worker(db_path)
    result = worker.run_once()
    assert result == {'ran': False, 'reason': 'disabled'}


# ── real end-to-end send against a real socket ───────────────────────────

def test_run_once_sends_a_queued_email_via_real_smtp(tmp_path, monkeypatch):
    with FakeSMTPServer() as server:
        monkeypatch.setenv('AURA_SMTP_HOST', server.host)
        monkeypatch.setenv('AURA_SMTP_PORT', str(server.port))
        monkeypatch.setenv('AURA_SMTP_USE_TLS', '0')
        monkeypatch.delenv('AURA_SMTP_USER', raising=False)
        monkeypatch.setenv('AURA_SMTP_FROM_ADDRESS', 'noreply@aura.test')

        db_path = _setup_db(tmp_path)
        _enqueue_directly(db_path, subject='Low stock: Widget', body_text='Stock is at 3 units.')

        worker = _make_worker(db_path)
        result = worker.run_once()

        assert result['ran'] is True
        assert result['claimed'] == 1
        assert result['outcomes']['sent'] == 1

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT status, sent_at FROM email_outbox").fetchone()
        assert row[0] == 'SENT'
        assert row[1]

        assert len(server.received) == 1
        assert 'owner@shop.test' in server.received[0]['rcpt_to'][0]
        assert 'Low stock: Widget' in server.received[0]['data']  # header, not base64-encoded
        assert 'Stock is at 3 units.' in _decoded_body(server.received[0]['data'])


def test_run_once_processes_multiple_queued_emails_in_one_pass(tmp_path, monkeypatch):
    with FakeSMTPServer() as server:
        monkeypatch.setenv('AURA_SMTP_HOST', server.host)
        monkeypatch.setenv('AURA_SMTP_PORT', str(server.port))
        monkeypatch.setenv('AURA_SMTP_USE_TLS', '0')
        monkeypatch.setenv('AURA_SMTP_FROM_ADDRESS', 'noreply@aura.test')

        db_path = _setup_db(tmp_path)
        _enqueue_directly(db_path, recipient='a@shop.test', subject='Alert A')
        _enqueue_directly(db_path, recipient='b@shop.test', subject='Alert B')

        worker = _make_worker(db_path)
        result = worker.run_once()

        assert result['claimed'] == 2
        assert result['outcomes']['sent'] == 2
        assert len(server.received) == 2


# ── failure handling ──────────────────────────────────────────────────────

def test_unreachable_smtp_server_retries_not_crashes(tmp_path, monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', '127.0.0.1')
    monkeypatch.setenv('AURA_SMTP_PORT', '1')  # nothing listens here
    monkeypatch.setenv('AURA_SMTP_USE_TLS', '0')

    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)

    worker = _make_worker(db_path)
    result = worker.run_once()  # must not raise

    assert result['outcomes']['retry'] == 1
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status, attempt_count, last_error, next_attempt_at FROM email_outbox").fetchone()
    assert row[0] == 'QUEUED'
    assert row[1] == 1
    assert row[2]  # some exception type name recorded
    assert row[3] is not None


def test_max_attempts_exceeded_goes_to_failed_permanent(tmp_path, monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', '127.0.0.1')
    monkeypatch.setenv('AURA_SMTP_PORT', '1')
    monkeypatch.setenv('AURA_SMTP_USE_TLS', '0')

    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    settings.set_setting(conn, 1, 'max_attempts', '2')
    conn.commit()
    conn.close()
    _enqueue_directly(db_path)

    worker = _make_worker(db_path)
    r1 = worker.run_once()
    assert r1['outcomes']['retry'] == 1

    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE email_outbox SET next_attempt_at=NULL")
    conn.commit()
    conn.close()

    r2 = worker.run_once()
    assert r2['outcomes']['failed_permanent'] == 1
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT status FROM email_outbox").fetchone()[0] == 'FAILED_PERMANENT'


def test_crash_recovery_reclaims_expired_lease_and_resends(tmp_path, monkeypatch):
    """Simulates a process that claimed a row (SENDING) then died before
    ever calling smtplib -- the next run_once() must reclaim the expired
    lease and actually attempt to send it (see outbox.py's own docstring
    for why this differs from einvoicing's check-status-first reclaim)."""
    with FakeSMTPServer() as server:
        monkeypatch.setenv('AURA_SMTP_HOST', server.host)
        monkeypatch.setenv('AURA_SMTP_PORT', str(server.port))
        monkeypatch.setenv('AURA_SMTP_USE_TLS', '0')
        monkeypatch.setenv('AURA_SMTP_FROM_ADDRESS', 'noreply@aura.test')

        db_path = _setup_db(tmp_path)
        _enqueue_directly(db_path)

        conn = sqlite3.connect(db_path)
        EmailOutboxRepository(conn).claim_due(company_id=1, batch_size=1, lease_seconds=-1)
        conn.commit()
        conn.close()

        worker = _make_worker(db_path)
        result = worker.run_once()

        assert result['reclaimed'] == 1
        assert result['outcomes']['sent'] == 1
        assert len(server.received) == 1


def test_backoff_delay_grows_and_is_capped():
    d0 = compute_backoff_delay(0)
    d1 = compute_backoff_delay(1)
    assert 24 <= d0 <= 36
    assert 48 <= d1 <= 72
    assert compute_backoff_delay(20) <= 3600 * 1.2


def test_start_and_stop_do_not_raise(tmp_path):
    db_path = _setup_db(tmp_path)
    worker = _make_worker(db_path)
    worker.start(interval_seconds=3600)
    worker.stop()
