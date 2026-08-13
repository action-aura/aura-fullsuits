"""Phase 9.5D Milestone 5/26 -- document numbering: format, per-year reset,
and real concurrency safety (no two threads ever get the same number)."""
from __future__ import annotations

import threading
from datetime import date

import pytest


def test_first_quote_number_of_the_year(app, seeded):
    with app.app_context():
        from app.commercial_sales.numbering import allocate_document_number
        from app.extensions import db_session

        number = allocate_document_number("QUOTE", as_of=date(2031, 1, 1))
        db_session.commit()
        assert number == "Q-2031-0001"


def test_sequential_numbers_increment(app, seeded):
    with app.app_context():
        from app.commercial_sales.numbering import allocate_document_number
        from app.extensions import db_session

        first = allocate_document_number("QUOTE", as_of=date(2032, 3, 1))
        second = allocate_document_number("QUOTE", as_of=date(2032, 3, 2))
        db_session.commit()
        assert first == "Q-2032-0001"
        assert second == "Q-2032-0002"


def test_different_document_types_have_independent_counters(app, seeded):
    with app.app_context():
        from app.commercial_sales.numbering import allocate_document_number
        from app.extensions import db_session

        quote_number = allocate_document_number("QUOTE", as_of=date(2033, 1, 1))
        order_number = allocate_document_number("SALES_ORDER", as_of=date(2033, 1, 1))
        db_session.commit()
        assert quote_number == "Q-2033-0001"
        assert order_number == "SO-2033-0001"


def test_year_boundary_resets_counter(app, seeded):
    with app.app_context():
        from app.commercial_sales.numbering import allocate_document_number
        from app.extensions import db_session

        allocate_document_number("QUOTE", as_of=date(2034, 12, 31))
        db_session.commit()
        next_year_first = allocate_document_number("QUOTE", as_of=date(2035, 1, 1))
        db_session.commit()
        assert next_year_first == "Q-2035-0001"


def test_unknown_document_type_rejected(app, seeded):
    with app.app_context():
        from app.commercial_sales.numbering import allocate_document_number

        with pytest.raises(ValueError):
            allocate_document_number("NOT_A_REAL_TYPE")


def test_concurrent_allocation_never_duplicates(app, seeded):
    """The real concurrency proof: N threads, each its own app_context and
    thread-local db_session (scoped_session), all racing to allocate a
    QUOTE number for the same year. SELECT ... FOR UPDATE must serialize
    them -- every returned number must be unique, and the sequence must be
    dense (1..N, no gaps, no duplicates)."""
    from app.extensions import db_session as scoped_db_session

    results: list[str] = []
    lock = threading.Lock()
    errors: list[Exception] = []

    def worker():
        try:
            with app.app_context():
                from app.commercial_sales.numbering import allocate_document_number

                number = allocate_document_number("QUOTE", as_of=date(2036, 6, 15))
                scoped_db_session.commit()
                with lock:
                    results.append(number)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            scoped_db_session.remove()

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"worker threads raised: {errors}"
    assert len(results) == 12
    assert len(set(results)) == 12, f"duplicate numbers allocated: {results}"

    values = sorted(int(n.split("-")[-1]) for n in results)
    assert values == list(range(1, 13)), f"non-dense/gapped sequence: {values}"
