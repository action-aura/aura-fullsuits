"""Aura Retail -- W4a core: the pieces purchase-order sync stands on, proven
directly against a real migrated database (no Flask, no HTTP).

WHY THESE TESTS EXIST. A purchase order used to live only on the computer that
typed it. W4a shares it between computers, which needs three things that must
be the same on every computer:

  * `recompute_po_paid` -- how much of an order is paid is DERIVED from the
    active supplier payments that name it, never a stored increment. A stored
    increment cannot be corrected by a void and double-counts when two
    computers each add their own instalment.
  * `receipt_movement_uid` -- the receipt's stock movement has a uid that is a
    pure function of (order, line), so two computers receiving the same order
    produce the SAME movement uid and the stock lands once.
  * the order journal is found by its `uid`, because the journal that arrives
    from a sibling has `source_id` NULL (its integer id means nothing here).

WHAT EACH GROUP GUARDS (and the mutation that must turn it RED):
  * recompute: paid summing a VOIDED payment, or another PO's payment.
  * receipt uid: uuid4 instead of uuid5, or ignoring the item.
  * journal lookup: `po_order_journal` ignoring `order_journal_uid`.

Run:
    pytest products/retail/tests/retail_po_sync_core_test.py -q
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP_DIRS = []
CID = 78
BRANCH = 1
CUR = 'JOD'
EPS = 0.0005        # half a fils: JOD is a 3-decimal currency


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _install():
    tmp = tempfile.mkdtemp(prefix='aura-retail-po-sync-core-')
    _TMP_DIRS.append(tmp)
    os.environ.update({'AURA_APP_DATA': tmp, 'AURA_STANDALONE': '1', 'AURA_SITE_RELAY_ENABLED': '0'})
    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    conn = sqlite3.connect(os.path.join(sch.SUBSYS_DIR, 'retail.db'), timeout=30)
    conn.row_factory = sqlite3.Row
    # `payments.related_type/related_id/status` are added lazily by retail_api.
    import api.retail_api as ra
    previous = ra._CREDIT_SCHEMA_READY
    ra._CREDIT_SCHEMA_READY = False
    try:
        ra._ensure_credit_schema(conn)
    finally:
        ra._CREDIT_SCHEMA_READY = previous
    return conn


@pytest.fixture
def conn():
    c = _install()
    yield c
    c.close()


@pytest.fixture
def gl():
    from core.retail import general_ledger as _gl
    return _gl


@pytest.fixture
def po_mod():
    from core.retail import purchase_orders as _po
    return _po


def _po(conn, total, number=None, status='pending'):
    uid = str(uuid.uuid4())
    cur = conn.execute(
        "INSERT INTO purchase_orders (company_id, po_number, branch_id, status, total, uid) "
        "VALUES (?,?,?,?,?,?)", (CID, number or 'PO-' + uid[:6], BRANCH, status, total, uid))
    return cur.lastrowid, uid


def _pay(conn, po_id, amount, status='active', company=CID):
    conn.execute(
        "INSERT INTO payments (company_id, method, amount, party_type, party_id, direction, "
        "related_type, related_id, status, uid) VALUES (?, 'cash', ?, 'supplier', 's1', 'out', 'po', ?, ?, ?)",
        (company, amount, po_id, status, str(uuid.uuid4())))


def _row(conn, po_id):
    return conn.execute("SELECT amount_paid, payment_status FROM purchase_orders WHERE id=?",
                        (po_id,)).fetchone()


# -- recompute_po_paid ---------------------------------------------------------

def test_unpaid_when_no_payment_names_the_order(conn, po_mod):
    po_id, _ = _po(conn, 100.0)
    assert po_mod.recompute_po_paid(conn, CID, po_id, EPS) == (0.0, 'unpaid')
    r = _row(conn, po_id)
    assert (r['amount_paid'], r['payment_status']) == (0.0, 'unpaid')


def test_partial_and_paid_are_written_back_to_the_row(conn, po_mod):
    po_id, _ = _po(conn, 100.0)
    _pay(conn, po_id, 30.0)
    _pay(conn, po_id, 20.0)
    assert po_mod.recompute_po_paid(conn, CID, po_id, EPS) == (50.0, 'partial')
    assert tuple(_row(conn, po_id)) == (50.0, 'partial')
    _pay(conn, po_id, 50.0)
    assert po_mod.recompute_po_paid(conn, CID, po_id, EPS) == (100.0, 'paid')
    assert tuple(_row(conn, po_id)) == (100.0, 'paid')


def test_a_voided_payment_is_not_counted(conn, po_mod):
    """THE W4a defect: a stored increment never went down when a payment was
    voided. Derived from ACTIVE payments, the void takes its amount back."""
    po_id, _ = _po(conn, 100.0)
    _pay(conn, po_id, 30.0)
    _pay(conn, po_id, 20.0, status='voided')
    assert po_mod.recompute_po_paid(conn, CID, po_id, EPS) == (30.0, 'partial')
    conn.execute("UPDATE payments SET status='voided' WHERE related_id=?", (po_id,))
    assert po_mod.recompute_po_paid(conn, CID, po_id, EPS) == (0.0, 'unpaid')


def test_a_payment_for_another_order_or_company_is_not_counted(conn, po_mod):
    po_id, _ = _po(conn, 100.0)
    other_id, _ = _po(conn, 100.0)
    _pay(conn, other_id, 40.0)
    _pay(conn, po_id, 10.0, company=CID + 1)
    _pay(conn, po_id, 5.0)
    assert po_mod.recompute_po_paid(conn, CID, po_id, EPS) == (5.0, 'partial')
    assert po_mod.recompute_po_paid(conn, CID, other_id, EPS) == (40.0, 'partial')


def test_epsilon_edge_one_fils_short_is_partial_half_fils_short_is_paid(conn, po_mod):
    po_id, _ = _po(conn, 10.000)
    _pay(conn, po_id, 9.999)           # one whole fils short: still owed
    assert po_mod.recompute_po_paid(conn, CID, po_id, EPS)[1] == 'partial'
    conn.execute("UPDATE payments SET amount=9.9996 WHERE related_id=?", (po_id,))
    assert po_mod.recompute_po_paid(conn, CID, po_id, EPS)[1] == 'paid'


def test_three_decimal_sum_has_no_float_drift(conn, po_mod):
    po_id, _ = _po(conn, 0.3)
    for _i in range(3):
        _pay(conn, po_id, 0.1)         # 0.1+0.1+0.1 != 0.3 in binary floats
    paid, status = po_mod.recompute_po_paid(conn, CID, po_id, EPS)
    assert paid == 0.3 and status == 'paid'


def test_a_zero_total_order_is_never_paid(conn, po_mod):
    po_id, _ = _po(conn, 0.0)
    assert po_mod.recompute_po_paid(conn, CID, po_id, EPS) == (0.0, 'unpaid')


# -- uids ------------------------------------------------------------------------

def test_receipt_movement_uid_is_deterministic_and_distinct_per_item(po_mod):
    a = po_mod.receipt_movement_uid('PO-U', 'ITEM-1')
    assert a == po_mod.receipt_movement_uid('PO-U', 'ITEM-1')
    assert a != po_mod.receipt_movement_uid('PO-U', 'ITEM-2')
    assert a != po_mod.receipt_movement_uid('PO-V', 'ITEM-1')
    assert str(uuid.UUID(a)) == a


def test_po_id_by_uid_is_company_scoped(conn, po_mod):
    po_id, uid = _po(conn, 10.0)
    assert po_mod.po_id_by_uid(conn, CID, uid) == po_id
    assert po_mod.po_id_by_uid(conn, CID + 1, uid) is None
    assert po_mod.po_id_by_uid(conn, CID, 'no-such-uid') is None


def test_status_rank_lets_received_beat_cancelled_beat_pending(po_mod):
    r = po_mod.PO_STATUS_RANK
    assert r['pending'] < r['cancelled'] < r['received']


# -- GL: the order journal is found by uid ---------------------------------------

def _order_journal_with_null_source(conn, gl, po_number='PO-SIB-1', amount=12.0):
    """What a SIBLING's synced order journal looks like here: posted with
    source_id NULL (its integer id means nothing on this computer)."""
    gl.ensure_default_chart(conn, CID)
    jid = gl.post_purchase_order(conn, CID, BRANCH, None, po_number, CUR, 0.0, amount)
    return jid, conn.execute("SELECT uid FROM gl_journals WHERE id=?", (jid,)).fetchone()['uid']


def test_po_order_journal_by_uid_finds_a_journal_with_null_source_id(conn, gl):
    jid, juid = _order_journal_with_null_source(conn, gl)
    assert gl.po_order_journal(conn, CID, 424242) is None, 'by integer id there is nothing to find'
    found = gl.po_order_journal(conn, CID, 424242, order_journal_uid=juid)
    assert found is not None and found['id'] == jid and found['debit_code'] == gl.ACCOUNT_GOODS_ORDERED


def test_po_order_journal_missing_distinguishes_not_arrived_from_no_uid(conn, gl):
    _jid, juid = _order_journal_with_null_source(conn, gl)
    assert gl.po_order_journal_missing(conn, CID, juid) is False
    assert gl.po_order_journal_missing(conn, CID, str(uuid.uuid4())) is True
    # a PO that never had an order journal (no uid recorded) is not "missing" one
    assert gl.po_order_journal_missing(conn, CID, None) is False
    assert gl.po_order_journal_missing(conn, CID + 1, juid) is True, 'company scoped'


def test_post_po_receipt_with_order_journal_uid_posts_once_when_source_id_is_null(conn, gl):
    _jid, juid = _order_journal_with_null_source(conn, gl, amount=12.0)
    first = gl.post_po_receipt(conn, CID, BRANCH, 424242, 'PO-SIB-1', CUR, order_journal_uid=juid)
    assert first is not None
    again = gl.post_po_receipt(conn, CID, BRANCH, 424242, 'PO-SIB-1', CUR, order_journal_uid=juid)
    assert again == first, 'once-only: a second receipt returns the existing journal'
    n = conn.execute("SELECT COUNT(*) FROM gl_journals WHERE company_id=? AND source_type='po_receipt'",
                     (CID,)).fetchone()[0]
    assert n == 1
    assert gl.post_po_receipt(conn, CID, BRANCH, 424242, 'PO-SIB-1', CUR) is None, \
        'without the uid there is no local-id link, so nothing is found (old behaviour kept)'


def test_post_purchase_order_stamps_the_po_uid_as_the_journal_source_uid(conn, gl):
    gl.ensure_default_chart(conn, CID)
    po_uid = str(uuid.uuid4())
    jid = gl.post_purchase_order(conn, CID, BRANCH, 7, 'PO-X', CUR, 0.0, 5.0, po_uid=po_uid)
    assert conn.execute("SELECT source_uid FROM gl_journals WHERE id=?", (jid,)).fetchone()[0] == po_uid


# -- stock_books: a sibling's purchase receipt is booked via the PO's journal uid --

def test_unbooked_count_matches_a_purchase_in_through_order_journal_uid(conn, gl):
    """Two computers both minted 'PO-0001' for DIFFERENT orders, and this one
    holds its own PO-0001 (order_journal_uid -> a journal that has not arrived
    here). The movement for it must stay unbooked; the same movement for a PO
    whose order journal IS here counts as booked even though the journal's
    source_id is NULL."""
    from core.retail import stock_books as sb
    gl.ensure_default_chart(conn, CID)
    _jid, juid = _order_journal_with_null_source(conn, gl, po_number='PO-0001', amount=4.0)
    po_id, _puid = _po(conn, 4.0, number='PO-0001')
    conn.execute("UPDATE purchase_orders SET order_journal_uid=? WHERE id=?", (juid, po_id))
    pid = str(uuid.uuid4())
    conn.execute("INSERT INTO products (id, company_id, name, sku, status) VALUES (?,?,?,?, 'active')",
                 (pid, CID, 'W', uuid.uuid4().hex[:8]))
    conn.execute("INSERT INTO inventory_movements (company_id, product_id, branch_id, movement_type, "
                 "quantity, unit_cost, reference, uid) VALUES (?,?,?, 'purchase_in', 2, 2, 'PO-0001', ?)",
                 (CID, pid, BRANCH, str(uuid.uuid4())))
    assert sb.unbooked_movement_count(conn, CID) == 0

    conn.execute("UPDATE purchase_orders SET order_journal_uid=? WHERE id=?", (str(uuid.uuid4()), po_id))
    assert sb.unbooked_movement_count(conn, CID) == 1, \
        'the PO exists here but its own order journal has not arrived: the text match must not book it'
