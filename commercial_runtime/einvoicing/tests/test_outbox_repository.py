import sqlite3
import threading

import pytest

from commercial_runtime.einvoicing.outbox import IllegalTransitionError, OutboxRepository
from commercial_runtime.einvoicing.schema import apply_einvoicing_schema


def _make_db(tmp_path):
    db_path = str(tmp_path / 'test.db')
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    apply_einvoicing_schema(conn)
    conn.commit()
    return db_path, conn


def _enqueue(repo, ref='ref-1', company_id=1, einvoice_no=None):
    # einvoice_no must be unique per (company_id, invoice_family) -- see
    # idx_einvoice_outbox_no. Default derives a distinct one from ref so
    # tests enqueueing multiple rows for the same company don't collide
    # with each other on this constraint (a different, separate constraint
    # from the invoice_ref uniqueness these tests are actually exercising).
    if einvoice_no is None:
        einvoice_no = f'INC-{abs(hash(ref)) % 1_000_000:06d}'
    return repo.enqueue(
        company_id=company_id, invoice_ref=ref, source_type='sale', source_id=1,
        local_document_no='SALE-1', einvoice_no=einvoice_no, invoice_family='income',
        payment_type='cash', currency='JOD', provider='mock',
    )


def test_enqueue_creates_a_queued_row(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    row_id = _enqueue(repo)
    conn.commit()
    assert row_id is not None
    row = repo.get_by_ref('ref-1')
    assert row['status'] == 'QUEUED'
    assert row['attempt_count'] == 0


def test_double_enqueue_same_ref_creates_only_one_row(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    id1 = _enqueue(repo)
    id2 = _enqueue(repo)  # same invoice_ref
    conn.commit()
    assert id1 is not None
    assert id2 is None, "duplicate enqueue must be a silent no-op"
    count = conn.execute("SELECT COUNT(*) FROM einvoice_outbox WHERE invoice_ref='ref-1'").fetchone()[0]
    assert count == 1


def test_claim_due_transitions_to_submitting(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    _enqueue(repo)
    conn.commit()
    claimed = repo.claim_due(batch_size=10, lease_seconds=60)
    conn.commit()
    assert len(claimed) == 1
    assert claimed[0]['status'] == 'SUBMITTING'
    assert claimed[0]['lease_expires_at'] is not None
    assert claimed[0]['submit_started_at'] is not None


def test_claim_due_does_not_reclaim_an_already_submitting_row(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    _enqueue(repo)
    conn.commit()
    first = repo.claim_due(batch_size=10, lease_seconds=60)
    conn.commit()
    second = repo.claim_due(batch_size=10, lease_seconds=60)
    conn.commit()
    assert len(first) == 1
    assert len(second) == 0


def test_concurrent_claim_never_double_claims_the_same_row(tmp_path):
    db_path, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    for i in range(10):
        _enqueue(repo, ref=f'ref-{i}')
    conn.commit()
    conn.close()

    results = []
    lock = threading.Lock()

    def _worker():
        c = sqlite3.connect(db_path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        r = OutboxRepository(c)
        claimed = r.claim_due(batch_size=10, lease_seconds=60)
        c.commit()
        c.close()
        with lock:
            results.extend(row['invoice_ref'] for row in claimed)

    threads = [threading.Thread(target=_worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 10, f"every row must be claimed exactly once total, got {len(results)}"
    assert len(set(results)) == 10, "no row was claimed by more than one worker"


def test_mark_cleared_from_submitting(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    row_id = _enqueue(repo)
    conn.commit()
    repo.claim_due(batch_size=10, lease_seconds=60)
    conn.commit()
    ok = repo.mark_cleared(row_id, provider_uuid='uuid-1', qr_payload='payload', qr_image_base64=None)
    conn.commit()
    assert ok
    row = repo.get_by_ref('ref-1')
    assert row['status'] == 'CLEARED'
    assert row['provider_uuid'] == 'uuid-1'
    assert row['cleared_at'] is not None


def test_mark_rejected_from_submitting(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    row_id = _enqueue(repo)
    conn.commit()
    repo.claim_due(batch_size=10, lease_seconds=60)
    conn.commit()
    ok = repo.mark_rejected(row_id, reason_code='BAD_TIN', detail='seller TIN invalid')
    conn.commit()
    assert ok
    row = repo.get_by_ref('ref-1')
    assert row['status'] == 'FAILED_PERMANENT'
    assert row['last_reason_code'] == 'BAD_TIN'


def test_mark_retry_increments_attempt_count_and_returns_to_queued(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    row_id = _enqueue(repo)
    conn.commit()
    repo.claim_due(batch_size=10, lease_seconds=60)
    conn.commit()
    ok = repo.mark_retry(row_id, reason_code='NETWORK', detail='timeout', next_attempt_at='2099-01-01T00:00:00+00:00')
    conn.commit()
    assert ok
    row = repo.get_by_ref('ref-1')
    assert row['status'] == 'QUEUED'
    assert row['attempt_count'] == 1
    assert row['next_attempt_at'] == '2099-01-01T00:00:00+00:00'


def test_retried_row_not_claimable_before_next_attempt_at(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    row_id = _enqueue(repo)
    conn.commit()
    repo.claim_due(batch_size=10, lease_seconds=60)
    conn.commit()
    repo.mark_retry(row_id, reason_code='NETWORK', detail=None, next_attempt_at='2099-01-01T00:00:00+00:00')
    conn.commit()
    claimed = repo.claim_due(batch_size=10, lease_seconds=60)
    conn.commit()
    assert claimed == [], "a row scheduled for the far future must not be claimed yet"


def test_mark_pending_then_resolve_cleared(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    row_id = _enqueue(repo)
    conn.commit()
    repo.claim_due(batch_size=10, lease_seconds=60)
    conn.commit()
    repo.mark_pending(row_id)
    conn.commit()
    assert repo.get_by_ref('ref-1')['status'] == 'AWAITING_CLEARANCE'
    repo.mark_cleared(row_id, provider_uuid='u', qr_payload='p', qr_image_base64=None)
    conn.commit()
    assert repo.get_by_ref('ref-1')['status'] == 'CLEARED'


def test_mark_unknown_then_resolve_not_received_returns_to_queued(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    row_id = _enqueue(repo)
    conn.commit()
    repo.claim_due(batch_size=10, lease_seconds=60)
    conn.commit()
    repo.mark_unknown(row_id, reason_code='CONN_DROPPED', detail=None)
    conn.commit()
    assert repo.get_by_ref('ref-1')['status'] == 'SUBMITTING_UNKNOWN'

    ok = repo.resolve_unknown_not_received(row_id, next_attempt_at='2020-01-01T00:00:00+00:00')
    conn.commit()
    assert ok
    row = repo.get_by_ref('ref-1')
    assert row['status'] == 'QUEUED'
    assert row['attempt_count'] == 1


def test_mark_unknown_then_resolve_cleared_never_touches_queued(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    row_id = _enqueue(repo)
    conn.commit()
    repo.claim_due(batch_size=10, lease_seconds=60)
    conn.commit()
    repo.mark_unknown(row_id, reason_code='CONN_DROPPED', detail=None)
    conn.commit()
    ok = repo.resolve_unknown_cleared(row_id, provider_uuid='u', qr_payload='p', qr_image_base64=None)
    conn.commit()
    assert ok
    assert repo.get_by_ref('ref-1')['status'] == 'CLEARED'


def test_illegal_transition_raises(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    row_id = _enqueue(repo)
    conn.commit()
    # Still QUEUED -- mark_cleared is only legal from SUBMITTING/AWAITING_CLEARANCE/SUBMITTING_UNKNOWN.
    with pytest.raises(IllegalTransitionError):
        repo.mark_cleared(row_id, provider_uuid='u', qr_payload='p', qr_image_base64=None)


def test_stale_transition_returns_false_not_raise(tmp_path):
    """Two callers both believing they can act on a SUBMITTING row: the
    second one to run against an already-CLEARED row gets False, not an
    exception and not a silent double-clear."""
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    row_id = _enqueue(repo)
    conn.commit()
    repo.claim_due(batch_size=10, lease_seconds=60)
    conn.commit()
    first = repo.mark_cleared(row_id, provider_uuid='u1', qr_payload='p', qr_image_base64=None)
    conn.commit()
    second = repo.mark_cleared(row_id, provider_uuid='u2', qr_payload='p2', qr_image_base64=None)
    conn.commit()
    assert first is True
    assert second is False
    row = repo.get_by_ref('ref-1')
    assert row['provider_uuid'] == 'u1', "the second, stale call must not have overwritten the first result"


def test_cancel_from_queued(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    row_id = _enqueue(repo)
    conn.commit()
    ok = repo.cancel(row_id)
    conn.commit()
    assert ok
    assert repo.get_by_ref('ref-1')['status'] == 'CANCELLED'


def test_cancel_from_terminal_state_fails(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    row_id = _enqueue(repo)
    conn.commit()
    repo.claim_due(batch_size=10, lease_seconds=60)
    conn.commit()
    repo.mark_cleared(row_id, provider_uuid='u', qr_payload='p', qr_image_base64=None)
    conn.commit()
    ok = repo.cancel(row_id)
    conn.commit()
    assert ok is False
    assert repo.get_by_ref('ref-1')['status'] == 'CLEARED'


def test_reclaim_expired_leases_goes_to_submitting_unknown_never_queued(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    row_id = _enqueue(repo)
    conn.commit()
    repo.claim_due(batch_size=10, lease_seconds=-1)  # already expired
    conn.commit()
    assert repo.get_by_ref('ref-1')['status'] == 'SUBMITTING'

    reclaimed = repo.reclaim_expired_leases()
    conn.commit()
    assert len(reclaimed) == 1
    row = repo.get_by_ref('ref-1')
    assert row['status'] == 'SUBMITTING_UNKNOWN', (
        "an expired lease must NEVER go straight back to QUEUED -- that would risk a "
        "second submission before confirming the first one wasn't received"
    )


def test_reclaim_expired_leases_ignores_fresh_leases(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    row_id = _enqueue(repo)
    conn.commit()
    repo.claim_due(batch_size=10, lease_seconds=3600)  # far future
    conn.commit()
    reclaimed = repo.reclaim_expired_leases()
    conn.commit()
    assert reclaimed == []
    assert repo.get_by_ref('ref-1')['status'] == 'SUBMITTING'


def test_counts_by_state(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    _enqueue(repo, ref='a')
    _enqueue(repo, ref='b')
    conn.commit()
    repo.claim_due(batch_size=1, lease_seconds=60)
    conn.commit()
    counts = repo.counts_by_state(company_id=1)
    assert counts['QUEUED'] == 1
    assert counts['SUBMITTING'] == 1
    assert counts['CLEARED'] == 0


def test_counts_by_state_scoped_per_company(tmp_path):
    _, conn = _make_db(tmp_path)
    repo = OutboxRepository(conn)
    _enqueue(repo, ref='a', company_id=1)
    _enqueue(repo, ref='b', company_id=2)
    conn.commit()
    assert repo.counts_by_state(company_id=1)['QUEUED'] == 1
    assert repo.counts_by_state(company_id=2)['QUEUED'] == 1
