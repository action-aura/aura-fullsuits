import sqlite3

import pytest

from commercial_runtime.notifications import tokens
from commercial_runtime.notifications.outbox import EmailOutboxRepository
from commercial_runtime.notifications.schema import apply_notifications_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    apply_notifications_schema(c)
    c.row_factory = sqlite3.Row
    return c


def test_generate_token_is_unique_and_reasonably_long():
    t1 = tokens.generate_token()
    t2 = tokens.generate_token()
    assert t1 != t2
    assert len(t1) >= 32


def test_hash_token_is_deterministic_and_not_reversible_looking():
    token = 'abc123'
    h1 = tokens.hash_token(token)
    h2 = tokens.hash_token(token)
    assert h1 == h2
    assert h1 != token
    assert len(h1) == 64  # sha256 hex digest


def test_queue_verification_email_enqueues_and_records_hash_not_raw_token(conn):
    result = tokens.queue_verification_email(conn, company_id=1, recipient='user@shop.test')

    assert 'token' in result and len(result['token']) >= 32
    assert result['email_outbox_id']

    outbox_row = EmailOutboxRepository(conn).get(result['email_outbox_id'])
    assert outbox_row['email_type'] == 'verification'
    assert outbox_row['recipient'] == 'user@shop.test'
    assert result['token'] in outbox_row['body_text'], "the raw token must be embedded in the email body"

    token_row = conn.execute(
        "SELECT * FROM email_verification_tokens WHERE email_outbox_id=?", (result['email_outbox_id'],)
    ).fetchone()
    assert token_row is not None
    assert token_row['token_hash'] == tokens.hash_token(result['token'])
    assert token_row['token_hash'] != result['token']
    assert token_row['consumed_at'] is None
    assert token_row['purpose'] == 'verify_email'


def test_queue_verification_email_respects_custom_purpose_and_ttl(conn):
    result = tokens.queue_verification_email(
        conn, company_id=1, recipient='user@shop.test', purpose='reset_password', ttl_minutes=5,
    )
    row = conn.execute(
        "SELECT purpose, expires_at, created_at FROM email_verification_tokens WHERE email_outbox_id=?",
        (result['email_outbox_id'],),
    ).fetchone()
    assert row['purpose'] == 'reset_password'
    assert row['expires_at'] > row['created_at']


def test_queue_verification_email_never_queues_the_email_synchronously_sent():
    """Structural check: queue_verification_email only ever writes rows via
    EmailOutboxRepository.enqueue() (status defaults to QUEUED) -- it must
    never itself call smtp_client.send_email or any other transport,
    keeping this on the same 'always via the queue' contract as the other
    two trigger points."""
    import inspect
    from commercial_runtime.notifications import tokens as tokens_module
    source = inspect.getsource(tokens_module)
    assert 'send_email' not in source
    assert 'smtplib' not in source
