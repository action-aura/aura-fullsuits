import sqlite3
import threading

import pytest

from commercial_runtime.einvoicing.schema import apply_einvoicing_schema
from commercial_runtime.einvoicing.sequence import (
    InvalidSeriesError,
    allocate_einvoice_number,
)


def _make_db(tmp_path):
    db_path = str(tmp_path / 'test.db')
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    apply_einvoicing_schema(conn)
    conn.commit()
    return db_path, conn


def test_first_allocation_is_000001(tmp_path):
    _, conn = _make_db(tmp_path)
    assert allocate_einvoice_number(conn, 1, 'income') == 'INC-000001'


def test_sequential_and_gapless_within_a_company_and_series(tmp_path):
    _, conn = _make_db(tmp_path)
    numbers = [allocate_einvoice_number(conn, 1, 'income') for _ in range(5)]
    assert numbers == ['INC-000001', 'INC-000002', 'INC-000003', 'INC-000004', 'INC-000005']


def test_series_are_independent_counters(tmp_path):
    _, conn = _make_db(tmp_path)
    assert allocate_einvoice_number(conn, 1, 'income') == 'INC-000001'
    assert allocate_einvoice_number(conn, 1, 'general_sales') == 'GS-000001'
    assert allocate_einvoice_number(conn, 1, 'income') == 'INC-000002'
    assert allocate_einvoice_number(conn, 1, 'general_sales') == 'GS-000002'


def test_per_company_isolation(tmp_path):
    _, conn = _make_db(tmp_path)
    assert allocate_einvoice_number(conn, 1, 'income') == 'INC-000001'
    assert allocate_einvoice_number(conn, 2, 'income') == 'INC-000001', (
        "company 2's first allocation must also start at 000001 -- "
        "independent counters, not a shared global sequence"
    )
    assert allocate_einvoice_number(conn, 1, 'income') == 'INC-000002'
    assert allocate_einvoice_number(conn, 2, 'income') == 'INC-000002'


def test_invalid_series_rejected(tmp_path):
    _, conn = _make_db(tmp_path)
    with pytest.raises(InvalidSeriesError):
        allocate_einvoice_number(conn, 1, 'not_a_real_series')


def test_rollback_of_caller_transaction_releases_the_number(tmp_path):
    """Mirrors _next_ref()'s contract exactly: this function does not manage
    its own transaction. If the caller's BEGIN IMMEDIATE rolls back, the
    counter increment rolls back with it -- the number was never truly
    'spent'."""
    db_path, conn = _make_db(tmp_path)

    conn.execute("BEGIN IMMEDIATE")
    first = allocate_einvoice_number(conn, 1, 'income')
    assert first == 'INC-000001'
    conn.rollback()

    conn.execute("BEGIN IMMEDIATE")
    second = allocate_einvoice_number(conn, 1, 'income')
    conn.commit()
    assert second == 'INC-000001', "rolled-back allocation must be reusable, proving no number was lost"


def test_committed_allocation_is_never_reused(tmp_path):
    db_path, conn = _make_db(tmp_path)
    conn.execute("BEGIN IMMEDIATE")
    first = allocate_einvoice_number(conn, 1, 'income')
    conn.commit()
    assert first == 'INC-000001'

    conn.execute("BEGIN IMMEDIATE")
    second = allocate_einvoice_number(conn, 1, 'income')
    conn.commit()
    assert second == 'INC-000002'


def test_concurrent_allocations_never_duplicate(tmp_path):
    """Two threads racing BEGIN IMMEDIATE against the same (company, series)
    counter must never produce the same number -- SQLite's IMMEDIATE lock
    serializes them, exactly as it already does for Retail's _next_ref()."""
    db_path, _ = _make_db(tmp_path)

    results = []
    errors = []
    lock = threading.Lock()

    def _worker():
        try:
            conn = sqlite3.connect(db_path, timeout=30)
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("BEGIN IMMEDIATE")
            number = allocate_einvoice_number(conn, 1, 'income')
            conn.commit()
            conn.close()
            with lock:
                results.append(number)
        except Exception as exc:  # pragma: no cover - failure path surfaced via assertion below
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"unexpected errors during concurrent allocation: {errors}"
    assert len(results) == 20
    assert len(set(results)) == 20, "every concurrently-allocated number must be unique"
    assert sorted(results) == [f'INC-{n:06d}' for n in range(1, 21)], "no gaps, no duplicates"
