"""The outbox must work against sqlcipher3, not only stdlib sqlite3.

THE BUG THIS PINS. `OutboxRepository` set `self._conn.row_factory =
sqlite3.Row` in three places. `sqlite3.Row` only accepts a `sqlite3.Cursor`,
so on a connection opened by a different DBAPI driver it raises

    TypeError: Row() argument 1 must be sqlite3.Cursor,
               not sqlcipher3.dbapi2.Cursor

and it raises at FETCH time rather than at assignment, so it presents as a
query bug rather than a driver mismatch.

WHY IT MATTERED AND WHY IT HID. A product in this suite encrypts its database
at rest and therefore opens it with sqlcipher3 on every real desktop install.
But `enqueue()` and `counts_by_state()` never touch `row_factory`, so queueing
an invoice and reporting outbox status both worked perfectly -- the two things
anyone integrating would try first. Only `claim_due()` and
`reclaim_expired_leases()` break, and those are what a SUBMISSION WORKER
calls. The worker would therefore have crashed on its first run in the field,
on a code path nothing in development exercised.

Found 2026-09-21 while wiring e-invoicing into Aura Dental, reproduced
directly against sqlcipher3 3.51.1, fixed at the source rather than worked
around at the call site.
"""
import sqlite3

import pytest

from commercial_runtime.einvoicing.outbox import _row_class_for

try:  # sqlcipher3 is not installed everywhere; skip rather than fail there
    import sqlcipher3
    _HAVE_SQLCIPHER = True
except ImportError:  # pragma: no cover - environment dependent
    sqlcipher3 = None
    _HAVE_SQLCIPHER = False


def _seeded(conn):
    conn.execute("CREATE TABLE t (a TEXT, b INTEGER)")
    conn.execute("INSERT INTO t VALUES ('x', 7)")
    return conn


def test_stdlib_sqlite3_still_gets_sqlite3_row():
    """The previous behaviour is preserved exactly for the common driver."""
    conn = _seeded(sqlite3.connect(":memory:"))
    assert _row_class_for(conn) is sqlite3.Row

    conn.row_factory = _row_class_for(conn)
    row = conn.execute("SELECT * FROM t").fetchone()
    assert row["a"] == "x" and row["b"] == 7
    assert row[0] == "x"          # index access, which plain dicts lack


@pytest.mark.skipif(not _HAVE_SQLCIPHER, reason="sqlcipher3 not installed here")
def test_sqlcipher3_gets_its_own_row_class_and_actually_fetches():
    """The regression itself: this raised TypeError before the fix."""
    conn = _seeded(sqlcipher3.dbapi2.connect(":memory:"))

    chosen = _row_class_for(conn)
    assert chosen is sqlcipher3.dbapi2.Row, (
        f"expected sqlcipher3's own Row, got {chosen!r} -- assigning stdlib "
        "sqlite3.Row to a sqlcipher3 connection raises at fetch time"
    )

    conn.row_factory = chosen
    row = conn.execute("SELECT * FROM t").fetchone()
    assert row["a"] == "x" and row["b"] == 7
    assert row[0] == "x"


@pytest.mark.skipif(not _HAVE_SQLCIPHER, reason="sqlcipher3 not installed here")
def test_the_old_hardcoded_behaviour_really_was_broken():
    """Proves the fix addresses a real failure rather than a theoretical one.

    If this ever stops raising, sqlcipher3 has become compatible with stdlib
    Row and `_row_class_for` is no longer load-bearing -- worth knowing before
    anyone simplifies it away.
    """
    conn = _seeded(sqlcipher3.dbapi2.connect(":memory:"))
    conn.row_factory = sqlite3.Row          # exactly what the code used to do
    with pytest.raises(TypeError, match="sqlite3.Cursor"):
        conn.execute("SELECT * FROM t").fetchone()


def test_falls_back_to_sqlite3_row_for_an_unknown_driver():
    """A driver exposing no Row of its own must not break the repository."""
    class FakeConn:
        pass

    FakeConn.__module__ = "a_driver_that_is_not_imported"
    assert _row_class_for(FakeConn()) is sqlite3.Row
