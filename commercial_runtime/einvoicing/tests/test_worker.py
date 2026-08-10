import sqlite3
import time

import pytest

from commercial_runtime.einvoicing import killswitch, settings
from commercial_runtime.einvoicing.document import BuyerId, EInvoiceDocument, EInvoiceLine
from commercial_runtime.einvoicing.providers.mock import MockProvider
from commercial_runtime.einvoicing.schema import apply_einvoicing_schema
from commercial_runtime.einvoicing.worker import OutboxWorker, compute_backoff_delay


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
    conn.execute("PRAGMA journal_mode=WAL")
    apply_einvoicing_schema(conn)
    if enabled:
        settings.set_setting(conn, company_id, 'enabled', '1')
    conn.commit()
    conn.close()
    return db_path


def _document_builder(conn, row):
    return EInvoiceDocument(
        company_id=1, einvoice_no=row['einvoice_no'], local_document_no=row['local_document_no'],
        invoice_family='income', payment_type='cash', currency='JOD',
        issue_datetime='2026-08-04T12:00:00', seller_name='Shop', seller_tin='1',
        buyer_name='Buyer', buyer_id=BuyerId('TIN', '2'),
        lines=(EInvoiceLine('Item', 1, 10.0, 0.0, 0.0, 10.0),),
        subtotal=10.0, discount_total=0.0, tax_total=0.0, grand_total=10.0,
    )


def _make_worker(db_path, tmp_path, provider, **overrides):
    kwargs = dict(
        conn_factory=_conn_factory(db_path),
        app_data_dir=str(tmp_path),
        company_id=1,
        provider=provider,
        document_builder=_document_builder,
        rate_limit_seconds=0,
    )
    kwargs.update(overrides)
    return OutboxWorker(**kwargs)


def _enqueue_directly(db_path, ref='ref-1', einvoice_no='INC-000001'):
    conn = sqlite3.connect(db_path)
    from commercial_runtime.einvoicing.outbox import OutboxRepository
    repo = OutboxRepository(conn)
    repo.enqueue(company_id=1, invoice_ref=ref, source_type='sale', source_id=1,
                 local_document_no='SALE-1', einvoice_no=einvoice_no, invoice_family='income',
                 payment_type='cash', currency='JOD', provider='mock')
    conn.commit()
    conn.close()


def test_run_once_does_nothing_when_disabled(tmp_path):
    db_path = _setup_db(tmp_path, enabled=False)
    _enqueue_directly(db_path)
    worker = _make_worker(db_path, tmp_path, MockProvider())
    result = worker.run_once()
    assert result == {'ran': False, 'reason': 'disabled'}
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT status FROM einvoice_outbox").fetchone()[0] == 'QUEUED'


def test_run_once_clears_a_queued_row(tmp_path):
    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)
    worker = _make_worker(db_path, tmp_path, MockProvider())
    result = worker.run_once()
    assert result['ran'] is True
    assert result['claimed'] == 1
    assert result['outcomes']['cleared'] == 1
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status, provider_uuid, document_xml FROM einvoice_outbox").fetchone()
    assert row[0] == 'CLEARED'
    assert row[1]
    assert row[2] and '<Invoice' in row[2]


def test_killswitch_stops_processing_even_when_db_setting_is_enabled(tmp_path):
    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)
    killswitch.set_disabled(str(tmp_path), reason='ops-pause')
    worker = _make_worker(db_path, tmp_path, MockProvider())
    result = worker.run_once()
    assert result == {'ran': False, 'reason': 'disabled'}


def test_killswitch_mid_run_takes_effect_on_next_tick(tmp_path):
    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path, ref='ref-1', einvoice_no='INC-1')
    worker = _make_worker(db_path, tmp_path, MockProvider())
    worker.run_once()  # clears ref-1

    _enqueue_directly(db_path, ref='ref-2', einvoice_no='INC-2')
    killswitch.set_disabled(str(tmp_path), reason='mid-run-pause')
    result = worker.run_once()
    assert result == {'ran': False, 'reason': 'disabled'}
    conn = sqlite3.connect(db_path)
    status = conn.execute("SELECT status FROM einvoice_outbox WHERE invoice_ref='ref-2'").fetchone()[0]
    assert status == 'QUEUED', "killswitch mid-run must prevent the next row from being touched at all"


def test_rejected_outcome_goes_to_failed_permanent(tmp_path):
    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)
    provider = MockProvider(outcome_script=['REJECTED'])
    worker = _make_worker(db_path, tmp_path, provider)
    result = worker.run_once()
    assert result['outcomes']['rejected'] == 1
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT status FROM einvoice_outbox").fetchone()[0] == 'FAILED_PERMANENT'


def test_retry_outcome_reschedules_with_incremented_attempt_count(tmp_path):
    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)
    provider = MockProvider(fail_first_n=1)
    worker = _make_worker(db_path, tmp_path, provider)
    result = worker.run_once()
    assert result['outcomes']['retry'] == 1
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status, attempt_count, next_attempt_at FROM einvoice_outbox").fetchone()
    assert row[0] == 'QUEUED'
    assert row[1] == 1
    assert row[2] is not None


def test_max_attempts_exceeded_goes_to_failed_permanent_not_infinite_retry(tmp_path):
    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    settings.set_setting(conn, 1, 'max_attempts', '2')
    conn.commit()
    conn.close()
    _enqueue_directly(db_path)

    provider = MockProvider(fail_first_n=999)  # always fails
    worker = _make_worker(db_path, tmp_path, provider, rate_limit_seconds=0)

    # Attempt 1: RETRY (attempt_count 0 -> 1, still under max_attempts=2)
    r1 = worker.run_once()
    assert r1['outcomes']['retry'] == 1
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status, attempt_count, next_attempt_at FROM einvoice_outbox").fetchone()
    assert row[0] == 'QUEUED' and row[1] == 1

    # Force it due again immediately for the test (bypass the real backoff wait).
    conn.execute("UPDATE einvoice_outbox SET next_attempt_at=NULL")
    conn.commit()
    conn.close()

    # Attempt 2: attempt_after = 1+1 = 2 >= max_attempts(2) -> FAILED_PERMANENT
    r2 = worker.run_once()
    assert r2['outcomes']['rejected'] == 1
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status, last_reason_code FROM einvoice_outbox").fetchone()
    assert row[0] == 'FAILED_PERMANENT'
    assert row[1] == 'MAX_ATTEMPTS_EXCEEDED'


def test_provider_exception_is_treated_as_retry_not_a_crash(tmp_path):
    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)

    class ExplodingProvider(MockProvider):
        def submit_invoice(self, invoice_ref, document):
            raise RuntimeError("simulated provider crash")

    worker = _make_worker(db_path, tmp_path, ExplodingProvider())
    result = worker.run_once()  # must not raise
    assert result['outcomes']['retry'] == 1
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status, last_reason_code FROM einvoice_outbox").fetchone()
    assert row[0] == 'QUEUED'
    assert row[1] == 'PROVIDER_EXCEPTION'


def test_document_builder_failure_retries_that_row_and_does_not_crash_the_pass(tmp_path):
    """A row whose document can't be built (e.g. missing buyer data) must
    degrade to a retry for THAT row only -- other claimed rows in the same
    pass must still be processed, and run_once() must not raise."""
    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path, ref='bad-ref', einvoice_no='INC-1')
    _enqueue_directly(db_path, ref='good-ref', einvoice_no='INC-2')

    def flaky_builder(conn, row):
        if row['invoice_ref'] == 'bad-ref':
            raise ValueError("no buyer id on file")
        return _document_builder(conn, row)

    worker = _make_worker(db_path, tmp_path, MockProvider(), document_builder=flaky_builder)
    result = worker.run_once()  # must not raise

    assert result['claimed'] == 2
    assert result['outcomes']['retry'] == 1
    assert result['outcomes']['cleared'] == 1

    conn = sqlite3.connect(db_path)
    bad = conn.execute("SELECT status, last_reason_code FROM einvoice_outbox WHERE invoice_ref='bad-ref'").fetchone()
    good = conn.execute("SELECT status FROM einvoice_outbox WHERE invoice_ref='good-ref'").fetchone()
    assert bad[0] == 'QUEUED'
    assert bad[1] == 'DOCUMENT_BUILD_FAILED'
    assert good[0] == 'CLEARED', "a later row in the same batch must still be processed after an earlier row's document-build failure"


def test_unknown_outcome_holds_the_row_without_resubmitting_same_pass(tmp_path):
    """A provider that can't confirm receipt (UNKNOWN) must leave the row
    in SUBMITTING_UNKNOWN, submitted exactly once -- never auto-resolved to
    CLEARED/QUEUED within the same pass without a real check_status answer."""
    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)
    provider = MockProvider(unknown_after_send=True)
    worker = _make_worker(db_path, tmp_path, provider)

    result = worker.run_once()
    assert result['outcomes']['unknown'] == 1
    assert provider._attempts.get('ref-1', 0) == 1
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT status FROM einvoice_outbox").fetchone()[0] == 'SUBMITTING_UNKNOWN'


def test_crash_recovery_lease_reclaim_resolves_via_check_status_not_a_resubmit(tmp_path):
    """The real crash scenario: a row was claimed (SUBMITTING) by a process
    that then died before ever calling submit_invoice for it (simulated
    here via a direct claim_due(), bypassing the worker entirely for the
    first 'attempt'). The next run_once() must reclaim it via the expired
    lease and resolve it using check_status -- submit_invoice must NEVER be
    called for this ref at all, proving the reclaim path cannot cause a
    double submission."""
    from commercial_runtime.einvoicing.outbox import OutboxRepository
    from commercial_runtime.einvoicing.providers.base import EInvoiceProvider, SubmissionResult

    class FixedStatusProvider(EInvoiceProvider):
        name = 'fixed-status'

        def __init__(self):
            self.submit_calls = []

        def submit_invoice(self, invoice_ref, document):
            self.submit_calls.append(invoice_ref)
            raise AssertionError("submit_invoice must never be called during lease-reclaim recovery")

        def check_status(self, invoice_ref, provider_uuid):
            return SubmissionResult(outcome='CLEARED', provider_uuid='real-uuid-from-istd', qr_payload='real-qr')

    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path)

    # Simulate the crash: claim the row (as the worker's claim_due step
    # would) with an already-expired lease, but never process it further.
    conn = sqlite3.connect(db_path)
    OutboxRepository(conn).claim_due(batch_size=1, lease_seconds=-1)
    conn.commit()
    conn.close()

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT status FROM einvoice_outbox").fetchone()[0] == 'SUBMITTING'
    conn.close()

    provider = FixedStatusProvider()
    worker = _make_worker(db_path, tmp_path, provider)
    result = worker.run_once()

    assert result['reclaimed'] == 1
    assert provider.submit_calls == [], "reclaim recovery must resolve via check_status, never submit_invoice"
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status, provider_uuid FROM einvoice_outbox").fetchone()
    assert row[0] == 'CLEARED'
    assert row[1] == 'real-uuid-from-istd'


def test_rate_limit_sleeps_between_multiple_submissions(tmp_path):
    db_path = _setup_db(tmp_path)
    _enqueue_directly(db_path, ref='ref-1', einvoice_no='INC-1')
    _enqueue_directly(db_path, ref='ref-2', einvoice_no='INC-2')
    worker = _make_worker(db_path, tmp_path, MockProvider(), rate_limit_seconds=0.2)
    start = time.monotonic()
    worker.run_once()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.2, "rate limit must introduce a real delay between submissions in the same pass"


def test_backoff_delay_grows_and_is_capped():
    d0 = compute_backoff_delay(0)
    d1 = compute_backoff_delay(1)
    d2 = compute_backoff_delay(2)
    assert 24 <= d0 <= 36       # ~30s +/-20%
    assert 48 <= d1 <= 72       # ~60s +/-20%
    assert 96 <= d2 <= 144      # ~120s +/-20%
    d_huge = compute_backoff_delay(20)
    assert d_huge <= 3600 * 1.2  # capped


def test_reconcile_fn_is_called_and_can_enqueue(tmp_path):
    db_path = _setup_db(tmp_path)
    calls = []

    def reconcile(conn, company_id, enabled_at_value):
        calls.append((company_id, enabled_at_value))

    worker = _make_worker(db_path, tmp_path, MockProvider(), reconcile_fn=reconcile)
    worker.run_once()
    assert len(calls) == 1
    assert calls[0][0] == 1


def test_start_and_stop_do_not_raise(tmp_path):
    db_path = _setup_db(tmp_path)
    worker = _make_worker(db_path, tmp_path, MockProvider())
    worker.start(interval_seconds=3600)
    worker.stop()
