import sqlite3

import pytest

from commercial_runtime.notifications.whatsapp_outbox import WhatsAppOutboxRepository
from commercial_runtime.notifications.schema import apply_notifications_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    apply_notifications_schema(c)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def repo(conn):
    return WhatsAppOutboxRepository(conn)


def _enqueue(repo, **overrides):
    kwargs = dict(
        company_id=1, message_type='low_stock_alert', recipient_phone_e164='+15551234567',
        template_name='aura_low_stock', language_code='en_US',
        component_params_json='["Widget", "3", "10", "Main"]',
    )
    kwargs.update(overrides)
    return repo.enqueue(**kwargs)


def test_enqueue_creates_a_queued_row(repo):
    row_id = _enqueue(repo)
    row = repo.get(row_id)
    assert row['status'] == 'QUEUED'
    assert row['attempt_count'] == 0
    assert row['recipient_phone_e164'] == '+15551234567'
    assert row['template_name'] == 'aura_low_stock'


def test_enqueue_always_inserts_no_dedup(repo):
    id1 = _enqueue(repo)
    id2 = _enqueue(repo)
    assert id1 != id2


def test_claim_due_moves_to_sending_and_is_exclusive(repo):
    row_id = _enqueue(repo)
    claimed = repo.claim_due(batch_size=10, lease_seconds=60)
    assert len(claimed) == 1
    assert claimed[0]['id'] == row_id
    assert repo.get(row_id)['status'] == 'SENDING'

    claimed_again = repo.claim_due(batch_size=10, lease_seconds=60)
    assert claimed_again == []


def test_mark_sent_records_wamid_and_is_terminal(repo):
    row_id = _enqueue(repo)
    repo.claim_due(batch_size=10, lease_seconds=60)
    assert repo.mark_sent(row_id, wamid='wamid.HBgLMTU1NTEyMzQ1NjcVAgARGB==') is True
    row = repo.get(row_id)
    assert row['status'] == 'SENT'
    assert row['sent_at']
    assert row['wamid'] == 'wamid.HBgLMTU1NTEyMzQ1NjcVAgARGB=='

    assert repo.mark_sent(row_id, wamid='ignored-second-call') is False


def test_mark_retry_increments_attempt_count_and_requeues(repo):
    row_id = _enqueue(repo)
    repo.claim_due(batch_size=10, lease_seconds=60)
    assert repo.mark_retry(row_id, error='WhatsAppSendError', next_attempt_at='2099-01-01T00:00:00+00:00') is True
    row = repo.get(row_id)
    assert row['status'] == 'QUEUED'
    assert row['attempt_count'] == 1
    assert row['last_error'] == 'WhatsAppSendError'
    assert row['next_attempt_at'] == '2099-01-01T00:00:00+00:00'


def test_mark_failed_permanent_is_terminal(repo):
    row_id = _enqueue(repo)
    repo.claim_due(batch_size=10, lease_seconds=60)
    assert repo.mark_failed_permanent(row_id, error='MaxAttemptsExceeded') is True
    row = repo.get(row_id)
    assert row['status'] == 'FAILED_PERMANENT'
    assert row['last_error'] == 'MaxAttemptsExceeded'


def test_cancel_moves_a_queued_row_to_cancelled(repo):
    row_id = _enqueue(repo)
    assert repo.cancel(row_id) is True
    assert repo.get(row_id)['status'] == 'CANCELLED'


def test_cancel_on_terminal_row_is_a_safe_noop(repo):
    row_id = _enqueue(repo)
    repo.claim_due(batch_size=10, lease_seconds=60)
    repo.mark_sent(row_id, wamid='x')
    assert repo.cancel(row_id) is False
    assert repo.get(row_id)['status'] == 'SENT'


def test_illegal_transition_raises(repo):
    row_id = _enqueue(repo)
    with pytest.raises(ValueError):
        repo.mark_sent(row_id, wamid='x')


def test_reclaim_expired_leases_returns_sending_rows_to_queued(repo):
    row_id = _enqueue(repo)
    repo.claim_due(batch_size=10, lease_seconds=-1)
    assert repo.get(row_id)['status'] == 'SENDING'

    reclaimed = repo.reclaim_expired_leases()
    assert reclaimed == 1
    row = repo.get(row_id)
    assert row['status'] == 'QUEUED'
    assert row['last_error'] == 'LEASE_EXPIRED'


def test_reclaim_expired_leases_ignores_rows_with_time_remaining(repo):
    row_id = _enqueue(repo)
    repo.claim_due(batch_size=10, lease_seconds=3600)
    assert repo.reclaim_expired_leases() == 0
    assert repo.get(row_id)['status'] == 'SENDING'


def test_counts_by_state(repo):
    _enqueue(repo)
    id2 = _enqueue(repo)
    repo.claim_due(batch_size=10, lease_seconds=60)
    repo.mark_sent(id2, wamid='x')

    counts = repo.counts_by_state(1)
    assert counts['QUEUED'] == 0
    assert counts['SENT'] == 1
    assert sum(counts.values()) == 2


def test_counts_by_state_scoped_per_company(repo):
    _enqueue(repo, company_id=1)
    _enqueue(repo, company_id=2)
    assert sum(repo.counts_by_state(1).values()) == 1
    assert sum(repo.counts_by_state(2).values()) == 1
