import logging
import sqlite3

import pytest

from commercial_runtime.notifications import whatsapp_settings
from commercial_runtime.notifications.whatsapp_outbox import WhatsAppOutboxRepository
from commercial_runtime.notifications.schema import apply_notifications_schema
from commercial_runtime.notifications.whatsapp_client import WhatsAppSendError
import commercial_runtime.notifications.whatsapp_worker as whatsapp_worker_module
from commercial_runtime.notifications.whatsapp_worker import WhatsAppOutboxWorker, compute_backoff_delay


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
        whatsapp_settings.set_setting(conn, company_id, 'enabled', '1')
    conn.commit()
    conn.close()
    return db_path


def _enqueue_directly(db_path, **overrides):
    conn = sqlite3.connect(db_path)
    kwargs = dict(
        company_id=1, message_type='low_stock_alert', recipient_phone_e164='+15551234567',
        template_name='aura_low_stock', language_code='en_US',
        component_params_json='["Widget", "3", "10", "Main"]',
    )
    kwargs.update(overrides)
    row_id = WhatsAppOutboxRepository(conn).enqueue(**kwargs)
    conn.commit()
    conn.close()
    return row_id


def _make_worker(db_path, **overrides):
    kwargs = dict(conn_factory=_conn_factory(db_path), company_id=1, rate_limit_seconds=0)
    kwargs.update(overrides)
    return WhatsAppOutboxWorker(**kwargs)


# ── inertness when unconfigured ──────────────────────────────────────────

def test_run_once_does_nothing_when_transport_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv('AURA_WHATSAPP_PHONE_NUMBER_ID', raising=False)
    db_path = _setup_db(tmp_path)  # company opted in, but transport still unconfigured
    _enqueue_directly(db_path)

    worker = _make_worker(db_path)
    result = worker.run_once()

    assert result == {'ran': False, 'reason': 'disabled'}
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT status FROM whatsapp_outbox").fetchone()[0] == 'QUEUED', \
        "an unconfigured install must never move a queued row toward SENDING"


def test_run_once_does_nothing_when_company_not_opted_in(tmp_path, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    db_path = _setup_db(tmp_path, enabled=False)
    _enqueue_directly(db_path)

    worker = _make_worker(db_path)
    result = worker.run_once()
    assert result == {'ran': False, 'reason': 'disabled'}


# ── send path (transport monkeypatched -- no real Graph API call) ────────

def test_run_once_sends_a_queued_message(tmp_path, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    calls = []

    def _fake_send(*, recipient_phone_e164, template_name, language_code, component_params):
        calls.append((recipient_phone_e164, template_name, language_code, component_params))
        return 'wamid.FAKE123'

    monkeypatch.setattr(whatsapp_worker_module, 'send_whatsapp_template_message', _fake_send)

    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)

    worker = _make_worker(db_path)
    result = worker.run_once()

    assert result['ran'] is True
    assert result['claimed'] == 1
    assert result['outcomes']['sent'] == 1

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status, sent_at, wamid FROM whatsapp_outbox").fetchone()
    assert row[0] == 'SENT'
    assert row[1]
    assert row[2] == 'wamid.FAKE123'

    assert len(calls) == 1
    phone, template, lang, params = calls[0]
    assert phone == '+15551234567'
    assert template == 'aura_low_stock'
    assert params == ['Widget', '3', '10', 'Main']


def test_run_once_processes_multiple_queued_messages_in_one_pass(tmp_path, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    monkeypatch.setattr(
        whatsapp_worker_module, 'send_whatsapp_template_message',
        lambda **kwargs: 'wamid.FAKE',
    )

    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path, recipient_phone_e164='+15550000001')
    _enqueue_directly(db_path, recipient_phone_e164='+15550000002')

    worker = _make_worker(db_path)
    result = worker.run_once()

    assert result['claimed'] == 2
    assert result['outcomes']['sent'] == 2


# ── failure handling ──────────────────────────────────────────────────────

def test_send_failure_retries_not_crashes(tmp_path, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')

    def _fake_send(**kwargs):
        raise WhatsAppSendError(401, {'message': 'Invalid OAuth access token', 'code': 190})

    monkeypatch.setattr(whatsapp_worker_module, 'send_whatsapp_template_message', _fake_send)

    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)

    worker = _make_worker(db_path)
    result = worker.run_once()  # must not raise

    assert result['outcomes']['retry'] == 1
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT status, attempt_count, last_error, next_attempt_at FROM whatsapp_outbox"
    ).fetchone()
    assert row[0] == 'QUEUED'
    assert row[1] == 1
    # last_error used to be the bare exception class name -- which made a bad
    # token (190), a non-allowlisted recipient (131030) and a DNS outage all
    # persist as the identical, undiagnosable string 'WhatsAppSendError'. It
    # now carries the HTTP status and Meta's own numeric code/message; the
    # numeric code is the part that must survive, since Meta's error docs are
    # indexed by it.
    assert 'HTTP 401' in row[2]
    assert '190' in row[2]
    assert 'Invalid OAuth access token' in row[2]
    assert row[3] is not None


def _stored_last_error(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT last_error FROM whatsapp_outbox").fetchone()[0]
    finally:
        conn.close()


def _run_one_failing_send(tmp_path, monkeypatch, exc):
    """Queues one row, makes the transport raise `exc`, and returns whatever
    _describe_send_failure() persisted to last_error."""
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')

    def _fake_send(**kwargs):
        raise exc

    monkeypatch.setattr(whatsapp_worker_module, 'send_whatsapp_template_message', _fake_send)

    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)
    _make_worker(db_path).run_once()
    return _stored_last_error(db_path)


def test_send_failure_from_a_non_api_exception_still_yields_a_useful_reason(tmp_path, monkeypatch):
    # A transport/config failure carries no Meta error code, so it takes
    # _describe_send_failure's `else` branch. That branch must still not
    # collapse back to a bare class name: in the queue UI a DNS outage and a
    # not-configured installation both used to read 'WhatsAppSendError'-style
    # single words with nothing to tell them apart.
    stored = _run_one_failing_send(
        tmp_path, monkeypatch,
        ConnectionError('HTTPSConnectionPool(host=graph.facebook.com, port=443): Max retries exceeded'),
    )

    assert stored.startswith('ConnectionError:'), stored
    assert stored != 'ConnectionError'
    assert 'graph.facebook.com' in stored


# ── credential redaction (AURA_WHATSAPP_ACCESS_TOKEN must never be stored) ──
#
# whatsapp_outbox.last_error is served by GET /api/notifications/whatsapp/outbox
# behind _require_session() and NOT _require_admin(), so every one of these is
# guarding a "any signed-in cashier can read the WhatsApp token" disclosure,
# not a cosmetic log-hygiene preference.

def test_send_failure_reason_never_leaks_the_configured_access_token(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    # The reproduction: a System User token pasted across two lines.
    # load_config_from_env() .strip()s only the ENDS, so the embedded CR/LF
    # rides into the Authorization header, and requests >= 2.32 rejects it by
    # interpolating the header VALUE into InvalidHeader's message.
    first_half = 'EAAG' + 'a' * 40
    second_half = 'b' * 40
    token = first_half + '\r\n' + second_half
    monkeypatch.setenv('AURA_WHATSAPP_ACCESS_TOKEN', token)

    def _fake_send(**kwargs):
        raise ValueError(
            "Invalid leading whitespace, reserved character(s), or return "
            "character(s) in header value: 'Bearer %s'" % token
        )

    monkeypatch.setattr(whatsapp_worker_module, 'send_whatsapp_template_message', _fake_send)

    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)

    with caplog.at_level(logging.WARNING, logger='aura.notifications.whatsapp'):
        _make_worker(db_path).run_once()

    stored = _stored_last_error(db_path)
    assert token not in stored
    assert first_half not in stored
    # The half AFTER the embedded newline is the one a `Bearer \S+` pattern
    # would stop short of and leave sitting in the clear.
    assert second_half not in stored, "greedy Bearer sweep must not stop at the embedded CR/LF"
    assert '<redacted>' in stored
    # The same string is handed to the app log by _process_row. A leak there is
    # every bit as real -- log files end up pasted into support tickets.
    assert first_half not in caplog.text
    assert second_half not in caplog.text


def test_send_failure_reason_redacts_the_token_even_without_a_bearer_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    token = 'EAAG' + 'c' * 60
    monkeypatch.setenv('AURA_WHATSAPP_ACCESS_TOKEN', token)

    # Not every leak carries the header name along with it -- a proxy or a
    # urllib3 retry message can echo the value on its own. The by-shape sweep
    # walks straight past this one; only the by-value pass catches it.
    stored = _run_one_failing_send(
        tmp_path, monkeypatch, ValueError('rejected credential ' + token + ' while connecting'),
    )

    assert token not in stored
    assert '<redacted>' in stored
    # And it must redact surgically: swallowing the whole message would put us
    # right back at the undiagnosable last_error this change exists to fix.
    assert 'while connecting' in stored


def test_send_failure_reason_redacts_a_bearer_token_that_is_no_longer_configured(tmp_path, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    monkeypatch.setenv('AURA_WHATSAPP_ACCESS_TOKEN', 'EAAG' + 'n' * 60)  # the NEW token
    # Rotating the env var is not atomic with an in-flight retry, so the value
    # in the exception can be a secret the by-value pass no longer recognises.
    # It is still a live-until-revoked credential; the by-shape sweep owns it.
    stale = 'EAAG' + 'o' * 60

    stored = _run_one_failing_send(
        tmp_path, monkeypatch, ValueError("in header value: 'Bearer %s'" % stale),
    )

    assert stale not in stored
    assert '<redacted>' in stored


def test_max_attempts_exceeded_goes_to_failed_permanent(tmp_path, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    monkeypatch.setattr(
        whatsapp_worker_module, 'send_whatsapp_template_message',
        lambda **kwargs: (_ for _ in ()).throw(WhatsAppSendError(401, {'message': 'bad token', 'code': 190})),
    )

    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    whatsapp_settings.set_setting(conn, 1, 'max_attempts', '2')
    conn.commit()
    conn.close()
    _enqueue_directly(db_path)

    worker = _make_worker(db_path)
    r1 = worker.run_once()
    assert r1['outcomes']['retry'] == 1

    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE whatsapp_outbox SET next_attempt_at=NULL")
    conn.commit()
    conn.close()

    r2 = worker.run_once()
    assert r2['outcomes']['failed_permanent'] == 1
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT status FROM whatsapp_outbox").fetchone()[0] == 'FAILED_PERMANENT'


def test_crash_recovery_reclaims_expired_lease_and_resends(tmp_path, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    monkeypatch.setattr(
        whatsapp_worker_module, 'send_whatsapp_template_message',
        lambda **kwargs: 'wamid.FAKE',
    )

    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)

    conn = sqlite3.connect(db_path)
    WhatsAppOutboxRepository(conn).claim_due(company_id=1, batch_size=1, lease_seconds=-1)
    conn.commit()
    conn.close()

    worker = _make_worker(db_path)
    result = worker.run_once()

    assert result['reclaimed'] == 1
    assert result['outcomes']['sent'] == 1


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
