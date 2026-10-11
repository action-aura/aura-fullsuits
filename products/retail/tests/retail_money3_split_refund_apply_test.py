"""MONEY-3 M1 leftovers (R1-R3 from 09446750's verifier): the sync-side refund-leg writer.

`_apply_return_refund_legs` (commercial_runtime/sync/sync_service.py) writes the legs of a pulled
`return`. Three things the verifier found loose, pinned here at the function itself (a bare
in-memory database -- these are decisions of one function, and the two-process test
retail_money3_split_refund_twodevice_test.py already covers the wire):

  R1  A legless re-send of a return that ALREADY has legs built the "legacy" leg anyway and added a
      bogus cash leg (the old till's event carries no `legs`; the legacy leg is a whole extra row).
      Now: a legless event never adds a leg to a return that has any. The other order -- the
      legacy leg is written first and the REAL legs arrive later -- also ends right: real legs
      SUPERSEDE the legacy leg, which is removed in the same apply. Why removed rather than kept:
      the legacy leg stands in for "the whole tender through the return's refund_method"; once the
      real per-method legs are known they already account for the same money, so keeping both
      counts it twice. The legacy leg's uid is deterministic (uuid5 of "<return uid>:legacy") so
      the remover names it exactly and can never touch a real leg.
  R2  Leg replay is `INSERT OR IGNORE` on the leg uid. A replay that BYPASSES the event-level
      dedup (the sync apply path's own `ON CONFLICT(id)`/applied-event ledger) must still add
      nothing, and must not rewrite an existing leg's amount.
  R3  A synced leg carries the RECEIVING computer's company_id, never anything the wire said.

MUTATIONS (each performed; red quoted in the crew report):
  R-i   `_apply_return_refund_legs`: build the legacy leg even when the return has legs   (R1 order 1)
  R-j   `_apply_return_refund_legs`: do not remove the legacy leg when real legs arrive   (R1 order 2)
  R-k   `_apply_return_refund_legs`: INSERT OR REPLACE instead of OR IGNORE               (R2)
  R-l   `_apply_return_refund_legs`: write the company_id the payload carries              (R3)

Run:
    pytest products/retail/tests/retail_money3_split_refund_apply_test.py -q
"""
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
SUITE_ROOT = PRODUCT_DIR.parent.parent
BACKEND_DIR = PRODUCT_DIR / 'backend'
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from commercial_runtime.sync import sync_service as ss  # noqa: E402
from database.schema import return_refund_leg_uid  # noqa: E402

RET_UID = 'ret-uid-0001'
LOCAL_COMPANY = 'LOCAL-CO'


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.execute("CREATE TABLE returns (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT)")
    c.execute("""CREATE TABLE return_refund_legs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL UNIQUE, company_id TEXT, branch_id INTEGER,
        return_id INTEGER NOT NULL, return_uid TEXT, method TEXT NOT NULL, amount REAL NOT NULL DEFAULT 0,
        created_at TEXT)""")
    c.execute("INSERT INTO returns (uid) VALUES (?)", (RET_UID,))
    return c


def _legs(conn):
    return sorted(conn.execute("SELECT method, amount, uid FROM return_refund_legs").fetchall())


def _real(method, amount):
    return {'uid': return_refund_leg_uid(RET_UID, method), 'method': method, 'amount': amount}


def _apply(conn, legs=None, company=LOCAL_COMPANY, refund_method='cash', tender=100.0, payload_extra=None):
    p = {'uid': RET_UID, 'refund_method': refund_method, 'created_at': '2026-10-01 10:00:00'}
    if legs is not None:
        p['legs'] = legs
    p.update(payload_extra or {})
    ss._apply_return_refund_legs(conn, p, company, 1, tender)


def test_the_twin_namespace_matches_the_schema_one():
    assert str(ss._RETURN_REFUND_LEG_NAMESPACE) == str(__import__('database.schema', fromlist=['x']).RETURN_REFUND_LEG_NAMESPACE)


def test_a_legless_resend_never_adds_a_leg_to_a_return_that_has_legs(conn):
    """R1, order 1: real legs first, then the old till's legless copy of the same return."""
    _apply(conn, legs=[_real('cash', 30.0), _real('card', 70.0)])
    assert [(m, a) for m, a, _ in _legs(conn)] == [('card', 70.0), ('cash', 30.0)]
    _apply(conn, legs=None, refund_method='cash', tender=100.0)
    assert [(m, a) for m, a, _ in _legs(conn)] == [('card', 70.0), ('cash', 30.0)], \
        'the legless re-send added a legacy leg on top of the real ones'


def test_real_legs_supersede_a_legacy_leg_written_first(conn):
    """R1, order 2: the legless event lands first (one legacy leg), the real legs arrive later."""
    _apply(conn, legs=None, refund_method='cash', tender=100.0)
    legacy = return_refund_leg_uid(RET_UID, 'legacy')
    assert [(m, a, u) for m, a, u in _legs(conn)] == [('cash', 100.0, legacy)]
    _apply(conn, legs=[_real('cash', 30.0), _real('card', 70.0)])
    assert [(m, a) for m, a, _ in _legs(conn)] == [('card', 70.0), ('cash', 30.0)], \
        'the legacy leg double-counted the money the real legs now explain'
    # the allow half: a legless event on a return with NO legs still gets its legacy leg
    conn.execute("INSERT INTO returns (uid) VALUES ('ret-other')")
    ss._apply_return_refund_legs(conn, {'uid': 'ret-other', 'refund_method': 'card'}, LOCAL_COMPANY, 1, 55.0)
    other = conn.execute("SELECT method, amount, uid FROM return_refund_legs WHERE return_uid='ret-other'").fetchall()
    assert other == [('card', 55.0, return_refund_leg_uid('ret-other', 'legacy'))]


def test_replaying_a_leg_adds_nothing_and_never_rewrites_it(conn):
    """R2: the same legs applied again -- with a DIFFERENT amount, as a corrupted replay would carry
    -- change nothing, even though no event-level dedup stands in the way."""
    _apply(conn, legs=[_real('cash', 30.0), _real('card', 70.0)])
    _apply(conn, legs=[_real('cash', 999.0), _real('card', 1.0)])
    assert [(m, a) for m, a, _ in _legs(conn)] == [('card', 70.0), ('cash', 30.0)]
    assert conn.execute("SELECT COUNT(*) FROM return_refund_legs").fetchone()[0] == 2


def test_a_synced_leg_carries_the_local_company_id(conn):
    """R3: the receiving computer's company id is what the row is filed under, whatever the wire
    (or a hostile peer) put in the leg."""
    leg = dict(_real('cash', 100.0), company_id='FOREIGN-CO')
    _apply(conn, legs=[leg], payload_extra={'company_id': 'FOREIGN-CO'})
    cos = conn.execute("SELECT DISTINCT company_id FROM return_refund_legs").fetchall()
    assert cos == [(LOCAL_COMPANY,)], cos


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
