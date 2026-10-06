"""Aura Retail -- W4a-2 core: partial receipts, supplier debt at RECEIPT, and
the books/balance rules that follow, proven against a real migrated v46
database (no Flask, no HTTP).

WHY THESE TESTS EXIST. Accounts Payable used to be booked when an order was
TYPED, so supplier balances claimed debt for goods that never came, and an
order could only be received whole. W4a-2 makes a RECEIPT an immutable synced
fact: received quantities, the order's state and the supplier's debt all
derive from receipts. Five rules must be the same on every computer and are
pinned here:

  * receipt uids are DETERMINISTIC (two computers recording the identical
    delivery offline mint the same uid and it counts once);
  * a receipt's VALUE is computed once by the writer and, when it completes the
    order, is the REMAINDER (total - earlier receipts) so receipts sum to the
    order total exactly;
  * the order's state (pending/partial/received/closed/cancelled) is DERIVED
    from receipts + the set-once cancelled_at/closed_at;
  * books per receipt: receipt basis Dr 1200 / Cr 2000; order basis moves the
    1260 the order parked, cumulatively rounded so the pieces sum to the whole;
    close short releases what never came;
  * supplier balance per order (party_balance) follows the same basis rules.

WHAT EACH GROUP GUARDS (and the mutation that must turn it RED):
  * uids: uuid4 instead of uuid5, or the sort of item uids dropped.
  * value: the remainder rule replaced by an independent rounding of each part.
  * state: closed beating received, or a cancelled order with receipts staying
    'cancelled'.
  * books: per-receipt independent rounding (the three pieces stop summing).
  * balance: counting an order-basis order at its total after it was closed.

Run:
    pytest products/retail/tests/retail_po_receipts_core_test.py -q
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
CID = 462
BRANCH = 1
CUR = 'JOD'
EPS = 0.0005
SUP = 'sup-1'


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _install():
    tmp = tempfile.mkdtemp(prefix='aura-retail-po-receipts-core-')
    _TMP_DIRS.append(tmp)
    os.environ.update({'AURA_APP_DATA': tmp, 'AURA_STANDALONE': '1', 'AURA_SITE_RELAY_ENABLED': '0'})
    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    conn = sqlite3.connect(os.path.join(sch.SUBSYS_DIR, 'retail.db'), timeout=30)
    conn.row_factory = sqlite3.Row
    import api.retail_api as ra
    previous = ra._CREDIT_SCHEMA_READY
    ra._CREDIT_SCHEMA_READY = False
    try:
        ra._ensure_credit_schema(conn)
    finally:
        ra._CREDIT_SCHEMA_READY = previous
    from core.retail import general_ledger as _gl
    _gl.ensure_default_chart(conn, CID)
    return conn


@pytest.fixture
def conn():
    c = _install()
    yield c
    c.close()


@pytest.fixture
def po_mod():
    from core.retail import purchase_orders as _po
    return _po


@pytest.fixture
def gl():
    from core.retail import general_ledger as _gl
    return _gl


@pytest.fixture
def pb():
    from core.retail import party_balance as _pb
    return _pb


def _po(conn, lines=((10, 3.0),), total=None, subtotal=None, basis='receipt',
        supplier=SUP, status='pending', closed_at=None, cancelled_at=None, number=None):
    """An order with `lines` = [(quantity, unit_cost)]. subtotal defaults to the
    sum of line totals, total to the subtotal (no tax)."""
    uid = str(uuid.uuid4())
    sub = subtotal if subtotal is not None else sum(q * c for q, c in lines)
    tot = total if total is not None else sub
    cur = conn.execute(
        "INSERT INTO purchase_orders (company_id, po_number, supplier_id, branch_id, status, subtotal, "
        "total, uid, ap_basis, closed_at, cancelled_at, item_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (CID, number or 'PO-' + uid[:6], supplier, BRANCH, status, sub, tot, uid, basis,
         closed_at, cancelled_at, len(lines)))
    po_id = cur.lastrowid
    items = []
    for q, c in lines:
        iuid = str(uuid.uuid4())
        icur = conn.execute(
            "INSERT INTO purchase_order_items (po_id, product_id, quantity, unit_cost, total, uid) "
            "VALUES (?,?,?,?,?,?)", (po_id, 'prod-' + iuid[:6], q, c, q * c, iuid))
        items.append((icur.lastrowid, iuid))
    return {'id': po_id, 'uid': uid, 'items': items}


def _receipt(conn, po_mod, po, qtys, value=None, received_at='2026-10-06 10:00:00'):
    """Record one receipt of `qtys` (one per line, 0 = line not in this
    delivery) the way the receive route will: deterministic uids, the value
    from `receipt_value`, then recompute the order's cached state."""
    n = conn.execute("SELECT COUNT(*) FROM purchase_order_receipts WHERE po_id=?", (po['id'],)).fetchone()[0]
    picked = [(iid, iuid, q) for (iid, iuid), q in zip(po['items'], qtys) if q]
    ruid = po_mod.receipt_uid(po['uid'], n, [(iuid, q) for _i, iuid, q in picked])
    if value is None:
        value = po_mod.receipt_value(conn, CID, po['id'], [(iid, q) for iid, _u, q in picked], CUR)
    cur = conn.execute(
        "INSERT INTO purchase_order_receipts (company_id, uid, po_id, branch_id, received_at, value, "
        "line_count, legacy) VALUES (?,?,?,?,?,?,?,0)",
        (CID, ruid, po['id'], BRANCH, received_at, float(value), len(picked)))
    for iid, iuid, q in picked:
        conn.execute(
            "INSERT INTO purchase_order_receipt_lines (receipt_id, uid, po_item_id, quantity, movement_uid) "
            "VALUES (?,?,?,?,?)",
            (cur.lastrowid, po_mod.receipt_line_uid(ruid, iuid), iid, q,
             po_mod.receipt_line_movement_uid(ruid, iuid)))
    po_mod.recompute_po_receive_state(conn, CID, po['id'])
    return ruid, Decimal(str(value))


def _status(conn, po):
    r = conn.execute("SELECT status, received_at FROM purchase_orders WHERE id=?", (po['id'],)).fetchone()
    return r['status'], r['received_at']


# -- uids -----------------------------------------------------------------------

def test_receipt_uid_is_deterministic_and_ignores_line_order(po_mod):
    a = po_mod.receipt_uid('PO-U', 0, [('I1', 3), ('I2', 4)])
    assert a == po_mod.receipt_uid('PO-U', 0, [('I2', 4), ('I1', 3)]), \
        'two computers listing the same delivery in a different order must mint the same uid'
    assert str(uuid.UUID(a)) == a


def test_receipt_uid_quantity_text_is_normalised(po_mod):
    base = po_mod.receipt_uid('PO-U', 0, [('I1', 6)])
    assert base == po_mod.receipt_uid('PO-U', 0, [('I1', 6.0)])
    assert base == po_mod.receipt_uid('PO-U', 0, [('I1', '6.000')])
    assert base == po_mod.receipt_uid('PO-U', 0, [('I1', Decimal('6.00'))])


def test_receipt_uid_differs_by_count_quantity_item_and_order(po_mod):
    base = po_mod.receipt_uid('PO-U', 0, [('I1', 6)])
    assert base != po_mod.receipt_uid('PO-U', 1, [('I1', 6)]), 'a second delivery of the same size is another fact'
    assert base != po_mod.receipt_uid('PO-U', 0, [('I1', 7)])
    assert base != po_mod.receipt_uid('PO-U', 0, [('I2', 6)])
    assert base != po_mod.receipt_uid('PO-V', 0, [('I1', 6)])


def test_line_movement_and_legacy_uids(po_mod):
    r = po_mod.receipt_uid('PO-U', 0, [('I1', 6)])
    assert po_mod.receipt_line_uid(r, 'I1') == po_mod.receipt_line_uid(r, 'I1')
    assert po_mod.receipt_line_uid(r, 'I1') != po_mod.receipt_line_uid(r, 'I2')
    assert po_mod.receipt_line_movement_uid(r, 'I1') != po_mod.receipt_line_uid(r, 'I1')
    assert po_mod.legacy_receipt_uid('PO-U') == po_mod.legacy_receipt_uid('PO-U')
    assert po_mod.legacy_receipt_uid('PO-U') != po_mod.legacy_receipt_uid('PO-V')


# -- receipt value ----------------------------------------------------------------

def test_partial_receipt_value_is_qty_times_cost_scaled_by_tax_factor(conn, po_mod):
    po = _po(conn, lines=((10, 3.0),), subtotal=30.0, total=33.0)       # 10% on top
    v = po_mod.receipt_value(conn, CID, po['id'], [(po['items'][0][0], 4)], CUR)
    assert v == Decimal('13.200')


def test_completing_receipt_takes_the_remainder_so_receipts_sum_to_total(conn, po_mod):
    """3 units at 33.333 (subtotal 99.999) with total 100.000: every part
    rounds independently to 33.333 and the three sum to 99.999 -- a fil short of
    the order. The completing receipt must be total - earlier parts (33.334)."""
    po = _po(conn, lines=((3, 33.333),), subtotal=99.999, total=100.0)
    iid = po['items'][0][0]
    _r1, v1 = _receipt(conn, po_mod, po, [1])
    _r2, v2 = _receipt(conn, po_mod, po, [1])
    v3 = po_mod.receipt_value(conn, CID, po['id'], [(iid, 1)], CUR)
    assert (v1, v2) == (Decimal('33.333'), Decimal('33.333'))
    assert v3 == Decimal('100.000') - v1 - v2 == Decimal('33.334')
    assert v1 + v2 + v3 == Decimal('100.000')


def test_remainder_counts_a_legacy_receipt_value(conn, po_mod):
    po = _po(conn, lines=((2, 5.0),))
    _receipt(conn, po_mod, po, [1], value=Decimal('4.000'))
    assert po_mod.receipt_value(conn, CID, po['id'], [(po['items'][0][0], 1)], CUR) == Decimal('6.000')


def test_receipt_value_refuses_a_line_of_another_order(conn, po_mod):
    po = _po(conn)
    other = _po(conn)
    with pytest.raises(ValueError):
        po_mod.receipt_value(conn, CID, po['id'], [(other['items'][0][0], 1)], CUR)


# -- derived state (D3) -----------------------------------------------------------

def test_state_no_receipts(conn, po_mod):
    pend = _po(conn)
    assert po_mod.recompute_po_receive_state(conn, CID, pend['id']) == 'pending'
    canc = _po(conn, cancelled_at='2026-10-01 00:00:00')
    assert po_mod.recompute_po_receive_state(conn, CID, canc['id']) == 'cancelled'
    # closed_at with no receipts cannot be produced by the routes -> cancelled
    odd = _po(conn, closed_at='2026-10-01 00:00:00')
    assert po_mod.recompute_po_receive_state(conn, CID, odd['id']) == 'cancelled'


def test_state_partial_closed_received(conn, po_mod):
    po = _po(conn, lines=((10, 1.0), (4, 2.0)))
    _receipt(conn, po_mod, po, [6, 0])
    assert _status(conn, po) == ('partial', None)
    conn.execute("UPDATE purchase_orders SET closed_at='2026-10-07 00:00:00' WHERE id=?", (po['id'],))
    assert po_mod.recompute_po_receive_state(conn, CID, po['id']) == 'closed'
    conn.execute("UPDATE purchase_orders SET closed_at=NULL, cancelled_at='2026-10-07 00:00:00' WHERE id=?",
                 (po['id'],))
    assert po_mod.recompute_po_receive_state(conn, CID, po['id']) == 'closed', \
        'cancelled after a partial delivery is a close-short, never "cancelled" (the stock is here)'
    conn.execute("UPDATE purchase_orders SET cancelled_at=NULL WHERE id=?", (po['id'],))
    _receipt(conn, po_mod, po, [4, 4], received_at='2026-10-08 09:00:00')
    assert _status(conn, po) == ('received', '2026-10-08 09:00:00')


def test_received_beats_closed_and_over_receipt_is_received(conn, po_mod):
    po = _po(conn, lines=((5, 1.0),))
    _receipt(conn, po_mod, po, [3], received_at='2026-10-06 10:00:00')
    _receipt(conn, po_mod, po, [3], received_at='2026-10-07 10:00:00')      # 6 of 5: over
    conn.execute("UPDATE purchase_orders SET closed_at='2026-10-09 00:00:00' WHERE id=?", (po['id'],))
    assert po_mod.recompute_po_receive_state(conn, CID, po['id']) == 'received'
    assert po_mod.po_over_received(conn, CID, po['id']) is True
    assert _status(conn, po)[1] == '2026-10-07 10:00:00', 'received_at = the latest receipt'


def test_recompute_writes_received_qty_from_receipts(conn, po_mod):
    po = _po(conn, lines=((10, 1.0), (4, 2.0)))
    _receipt(conn, po_mod, po, [6, 1])
    _receipt(conn, po_mod, po, [2, 0])
    got = [r[0] for r in conn.execute(
        "SELECT received_qty FROM purchase_order_items WHERE po_id=? ORDER BY id", (po['id'],))]
    assert got == [8.0, 1.0]
    assert po_mod.received_by_item(conn, CID, po['id']) == {po['items'][0][0]: Decimal('8'),
                                                            po['items'][1][0]: Decimal('1')}


def test_recompute_is_company_scoped(conn, po_mod):
    po = _po(conn)
    _receipt(conn, po_mod, po, [3])
    assert po_mod.recompute_po_receive_state(conn, CID + 1, po['id']) is None, \
        'another company asking about this order changes nothing and answers nothing'
    assert _status(conn, po)[0] == 'partial'


# -- books (D6, D7, D8) -----------------------------------------------------------

def _journal_lines(conn, journal_id):
    return {(r['code'], 'dr' if r['debit'] else 'cr'): Decimal(str(r['debit'] or r['credit']))
            for r in conn.execute(
                "SELECT a.code AS code, l.debit AS debit, l.credit AS credit FROM gl_journal_lines l "
                "JOIN gl_accounts a ON a.id=l.account_id WHERE l.journal_id=?", (journal_id,))}


def _po_row(conn, po):
    return conn.execute("SELECT * FROM purchase_orders WHERE id=?", (po['id'],)).fetchone()


def test_receipt_basis_books_inventory_against_ap(conn, po_mod, gl):
    po = _po(conn, lines=((10, 3.0),))
    ruid, value = _receipt(conn, po_mod, po, [6])
    jid = gl.post_po_receipt_for(conn, CID, BRANCH, _po_row(conn, po), ruid, value, CUR)
    assert _journal_lines(conn, jid) == {('1200', 'dr'): Decimal('18'), ('2000', 'cr'): Decimal('18')}
    j = conn.execute("SELECT * FROM gl_journals WHERE id=?", (jid,)).fetchone()
    assert (j['source_type'], j['source_uid'], j['uid']) == \
        ('po_receipt', ruid, gl.stock_journal_uid(ruid, 'po_receipt'))
    # once-only: a retry returns the same journal
    assert gl.post_po_receipt_for(conn, CID, BRANCH, _po_row(conn, po), ruid, value, CUR) == jid


def test_receipt_basis_without_supplier_credits_goods_ordered(conn, po_mod, gl):
    po = _po(conn, lines=((10, 3.0),), supplier=None)
    ruid, value = _receipt(conn, po_mod, po, [6])
    jid = gl.post_po_receipt_for(conn, CID, BRANCH, _po_row(conn, po), ruid, value, CUR)
    assert _journal_lines(conn, jid) == {('1200', 'dr'): Decimal('18'), ('1260', 'cr'): Decimal('18')}


def test_zero_value_receipt_posts_nothing(conn, po_mod, gl):
    po = _po(conn, lines=((10, 0.0),))
    ruid, value = _receipt(conn, po_mod, po, [6])
    assert gl.post_po_receipt_for(conn, CID, BRANCH, _po_row(conn, po), ruid, value, CUR) is None


def _order_basis_po(conn, gl, lines, order_debit, total=None, paid=0.0):
    po = _po(conn, lines=lines, total=total, basis='order')
    jid = gl.post_purchase_order(conn, CID, BRANCH, po['id'], 'PO-ORD-' + po['uid'][:4], CUR,
                                 paid, order_debit - paid, po_uid=po['uid'])
    juid = conn.execute("SELECT uid FROM gl_journals WHERE id=?", (jid,)).fetchone()['uid']
    conn.execute("UPDATE purchase_orders SET order_journal_uid=? WHERE id=?", (juid, po['id']))
    return po


@pytest.mark.parametrize('order_debit', [100.0, 10.0], ids=['debit-equals-total', 'debit-is-a-tenth'])
def test_order_basis_cumulative_rounding_sums_to_the_order_debit(conn, po_mod, gl, order_debit):
    """THE ROUNDING RULE. Three receipts of a third each; each journal is
    round(debit x cumulative/total) minus what earlier receipts already moved,
    so the three pieces sum to the order's own 1260 debit EXACTLY -- rounding
    each third independently (3.333 x 3 = 9.999) leaves 1260 stuck."""
    po = _order_basis_po(conn, gl, ((3, 33.333),), order_debit, total=100.0)
    moved = []
    for q, val in ((1, '33.333'), (1, '33.333'), (1, None)):
        ruid, value = _receipt(conn, po_mod, po, [q], value=Decimal(val) if val else None)
        jid = gl.post_po_receipt_for(conn, CID, BRANCH, _po_row(conn, po), ruid, value, CUR)
        assert jid is not None
        lines = _journal_lines(conn, jid)
        assert set(lines) == {('1200', 'dr'), ('1260', 'cr')}
        moved.append(lines[('1200', 'dr')])
    assert sum(moved) == Decimal(str(order_debit)).quantize(Decimal('0.001'))
    if order_debit == 10.0:
        assert moved == [Decimal('3.333'), Decimal('3.334'), Decimal('3.333')]
    assert gl.po_receipt_moved(conn, CID, _po_row(conn, po)) == sum(moved)


def test_order_basis_legacy_one_shot_receipt_journal_counts_as_moved(conn, po_mod, gl):
    po = _order_basis_po(conn, gl, ((2, 50.0),), 100.0)
    row = _po_row(conn, po)
    gl.post_po_receipt(conn, CID, BRANCH, po['id'], row['po_number'], CUR,
                       order_journal_uid=row['order_journal_uid'])
    assert gl.po_receipt_moved(conn, CID, _po_row(conn, po)) == Decimal('100.000')
    # nothing is left for a close-short to release
    assert gl.post_po_close_short(conn, CID, BRANCH, _po_row(conn, po), CUR) is None


def test_order_basis_with_inventory_debit_posts_nothing_per_receipt(conn, po_mod, gl):
    """A pre-STOCK-BOOKS order debited Inventory directly at order time."""
    po = _po(conn, lines=((2, 50.0),), basis='order')
    gl.ensure_default_chart(conn, CID)
    jid = gl.post_journal(conn, CID, BRANCH, '2026-10-01', 'purchase_order', po['id'], CUR,
                          [{'account_code': '1200', 'debit': 100.0}, {'account_code': '2000', 'credit': 100.0}],
                          reference='PO-L', source_uid=po['uid'])
    conn.execute("UPDATE purchase_orders SET order_journal_uid=(SELECT uid FROM gl_journals WHERE id=?) "
                 "WHERE id=?", (jid, po['id']))
    ruid, value = _receipt(conn, po_mod, po, [1])
    assert gl.post_po_receipt_for(conn, CID, BRANCH, _po_row(conn, po), ruid, value, CUR) is None


def test_prepayment_debits_ap_or_goods_ordered_and_credits_cash(conn, gl):
    jid = gl.post_po_prepayment(conn, CID, BRANCH, 7, True, Decimal('25.000'), 'pay-uid-1', CUR)
    assert _journal_lines(conn, jid) == {('2000', 'dr'): Decimal('25'), ('1000', 'cr'): Decimal('25')}
    j = conn.execute("SELECT * FROM gl_journals WHERE id=?", (jid,)).fetchone()
    assert (j['source_type'], j['source_id'], j['source_uid']) == ('po_payment', 7, 'pay-uid-1')
    jid2 = gl.post_po_prepayment(conn, CID, BRANCH, 8, False, Decimal('5.000'), 'pay-uid-2', CUR)
    assert _journal_lines(conn, jid2) == {('1260', 'dr'): Decimal('5'), ('1000', 'cr'): Decimal('5')}
    assert gl.post_po_prepayment(conn, CID, BRANCH, 9, True, Decimal('0'), 'pay-uid-3', CUR) is None


def test_close_short_order_basis_releases_the_unreceived_share(conn, po_mod, gl):
    po = _order_basis_po(conn, gl, ((10, 10.0),), 100.0)
    ruid, value = _receipt(conn, po_mod, po, [6])
    assert value == Decimal('60.000')
    gl.post_po_receipt_for(conn, CID, BRANCH, _po_row(conn, po), ruid, value, CUR)
    conn.execute("UPDATE purchase_orders SET closed_at='2026-10-07 00:00:00' WHERE id=?", (po['id'],))
    jid = gl.post_po_close_short(conn, CID, BRANCH, _po_row(conn, po), CUR)
    assert _journal_lines(conn, jid) == {('2000', 'dr'): Decimal('40'), ('1260', 'cr'): Decimal('40')}
    j = conn.execute("SELECT * FROM gl_journals WHERE id=?", (jid,)).fetchone()
    assert (j['source_type'], j['source_uid'], j['uid']) == \
        ('po_close_short', po['uid'], gl.stock_journal_uid(po['uid'], 'po_close_short'))
    assert gl.post_po_close_short(conn, CID, BRANCH, _po_row(conn, po), CUR) == jid, 'once-only'


def test_close_short_on_a_legacy_inventory_debit_order_credits_inventory(conn, po_mod, gl):
    po = _po(conn, lines=((10, 10.0),), basis='order')
    jid = gl.post_journal(conn, CID, BRANCH, '2026-10-01', 'purchase_order', po['id'], CUR,
                          [{'account_code': '1200', 'debit': 100.0}, {'account_code': '2000', 'credit': 100.0}],
                          reference='PO-L2', source_uid=po['uid'])
    conn.execute("UPDATE purchase_orders SET order_journal_uid=(SELECT uid FROM gl_journals WHERE id=?) "
                 "WHERE id=?", (jid, po['id']))
    _receipt(conn, po_mod, po, [6])
    conn.execute("UPDATE purchase_orders SET closed_at='2026-10-07 00:00:00' WHERE id=?", (po['id'],))
    cj = gl.post_po_close_short(conn, CID, BRANCH, _po_row(conn, po), CUR)
    # only the 40 that never arrived leaves Inventory; the 60 that did stays
    assert _journal_lines(conn, cj) == {('2000', 'dr'): Decimal('40'), ('1200', 'cr'): Decimal('40')}


def test_close_short_receipt_basis_posts_nothing(conn, po_mod, gl):
    po = _po(conn, lines=((10, 10.0),))
    _receipt(conn, po_mod, po, [6])
    assert gl.post_po_close_short(conn, CID, BRANCH, _po_row(conn, po), CUR) is None


# -- stock_books: movement booked by its receipt's journal ------------------------

def _movement(conn, po, ruid, po_mod, item_index=0):
    iuid = po['items'][item_index][1]
    muid = po_mod.receipt_line_movement_uid(ruid, iuid)
    conn.execute(
        "INSERT INTO inventory_movements (company_id, product_id, branch_id, movement_type, quantity, "
        "unit_cost, reference, uid) VALUES (?,?,?, 'purchase_in', 6, 3, ?, ?)",
        (CID, 'prod-x', BRANCH, _po_row(conn, po)['po_number'], muid))


def test_unbooked_count_books_a_receipt_movement_through_its_receipt_journal(conn, po_mod, gl):
    from core.retail import stock_books as sb
    po = _po(conn, lines=((10, 3.0),))
    ruid, value = _receipt(conn, po_mod, po, [6])
    _movement(conn, po, ruid, po_mod)
    assert sb.unbooked_movement_count(conn, CID) == 1, 'receipt exists, nothing posted yet'
    gl.post_po_receipt_for(conn, CID, BRANCH, _po_row(conn, po), ruid, value, CUR)
    assert sb.unbooked_movement_count(conn, CID) == 0


def test_unbooked_count_zero_value_receipt_is_not_unbooked(conn, po_mod):
    from core.retail import stock_books as sb
    po = _po(conn, lines=((10, 0.0),))
    ruid, _v = _receipt(conn, po_mod, po, [6])
    _movement(conn, po, ruid, po_mod)
    assert sb.unbooked_movement_count(conn, CID) == 0, \
        'a zero-cost receipt is never journalled (unknown cost is silence), so it must not read "not opened"'


# -- party_balance: the supplier formula (D10) ------------------------------------

def _bal(pb, conn):
    return pb.supplier_balance(conn, CID, SUP, CUR)


def test_balance_order_basis_states(conn, po_mod, pb):
    pend = _po(conn, lines=((10, 10.0),), basis='order')
    assert _bal(pb, conn) == Decimal('100.000')
    _receipt(conn, po_mod, pend, [6])
    assert _bal(pb, conn) == Decimal('100.000'), 'partial, still open: the whole order is owed'
    conn.execute("UPDATE purchase_orders SET closed_at='2026-10-07 00:00:00' WHERE id=?", (pend['id'],))
    assert _bal(pb, conn) == Decimal('60.000'), 'closed short: only what arrived'
    canc = _po(conn, lines=((1, 7.0),), basis='order', cancelled_at='2026-10-07 00:00:00', status='cancelled')
    assert _bal(pb, conn) == Decimal('60.000'), 'a cancelled order is not owed'
    _po(conn, lines=((1, 5.0),), basis=None)           # NULL basis behaves as 'order'
    assert _bal(pb, conn) == Decimal('65.000')
    assert canc['id']


def test_balance_receipt_basis_follows_receipts_only(conn, po_mod, pb):
    po = _po(conn, lines=((10, 10.0),))
    assert _bal(pb, conn) == Decimal('0.000'), 'nothing has arrived, nothing is owed'
    _receipt(conn, po_mod, po, [6])
    assert _bal(pb, conn) == Decimal('60.000')
    _receipt(conn, po_mod, po, [4])
    assert _bal(pb, conn) == Decimal('100.000'), 'fully received: exactly the total'
    conn.execute("UPDATE purchase_orders SET closed_at='2026-10-07 00:00:00' WHERE id=?", (po['id'],))
    assert _bal(pb, conn) == Decimal('100.000')


def test_balance_receipt_basis_closed_short_and_prepayment(conn, po_mod, pb):
    po = _po(conn, lines=((10, 10.0),))
    conn.execute(
        "INSERT INTO payments (company_id, method, amount, party_type, party_id, direction, related_type, "
        "related_id, status, uid) VALUES (?, 'cash', 30, 'supplier', ?, 'out', 'po', ?, 'active', ?)",
        (CID, SUP, po['id'], str(uuid.uuid4())))
    assert _bal(pb, conn) == Decimal('-30.000'), 'paid before delivery: the supplier owes the shop (an advance)'
    _receipt(conn, po_mod, po, [6])
    conn.execute("UPDATE purchase_orders SET closed_at='2026-10-07 00:00:00' WHERE id=?", (po['id'],))
    assert _bal(pb, conn) == Decimal('30.000')


def test_charge_helper_agrees_with_the_aggregate_for_every_basis_and_state(conn, po_mod, pb):
    pos = [
        _po(conn, lines=((10, 10.0),), basis='order'),
        _po(conn, lines=((10, 10.0),), basis='order'),
        _po(conn, lines=((10, 10.0),), basis='order', cancelled_at='2026-10-02 00:00:00', status='cancelled'),
        _po(conn, lines=((10, 10.0),)),
        _po(conn, lines=((10, 10.0),)),
    ]
    _receipt(conn, po_mod, pos[1], [6])
    conn.execute("UPDATE purchase_orders SET closed_at='2026-10-07 00:00:00' WHERE id=?", (pos[1]['id'],))
    _receipt(conn, po_mod, pos[3], [10])
    _receipt(conn, po_mod, pos[4], [3])
    total_minor = sum(po_mod.po_supplier_charge_minor(conn, CID, p['id'], EPS) for p in pos)
    assert Decimal(total_minor) / Decimal(1000) == _bal(pb, conn)
    assert [po_mod.po_supplier_charge_minor(conn, CID, p['id'], EPS) for p in pos] == \
        [100000, 60000, 0, 100000, 30000]


def test_balance_ignores_other_companies_receipts(conn, po_mod, pb):
    po = _po(conn, lines=((10, 10.0),))
    _receipt(conn, po_mod, po, [6])
    conn.execute("UPDATE purchase_order_receipts SET company_id=? WHERE po_id=?", (CID + 1, po['id']))
    assert _bal(pb, conn) == Decimal('0.000')


# -- stock books: the per-receipt link (W4a-2 fix, F5) ----------------------------

def _receipt_movement(conn, po, ruid, line_idx, qty):
    """The stock movement a receipt line wrote: uid = the line's movement uid."""
    from core.retail import purchase_orders as _po
    row = conn.execute("SELECT po_number FROM purchase_orders WHERE id=?", (po['id'],)).fetchone()
    conn.execute(
        "INSERT INTO inventory_movements (company_id, product_id, branch_id, movement_type, quantity, "
        "unit_cost, reference, uid) VALUES (?,?,?,'purchase_in',?,3.0,?,?)",
        (CID, 'prod-x', BRANCH, qty, row['po_number'],
         _po.receipt_line_movement_uid(ruid, po['items'][line_idx][1])))


def test_second_receipts_missing_journal_leaves_its_movement_unbooked(conn, po_mod, gl):
    """W4a-2 fix (F5). Two receipts of one receipt-basis order; only the FIRST
    has its po_receipt journal on this computer (the second's has not synced).
    The per-order fallback (a journal whose source_id is the order's id) used
    to mark BOTH movements booked, hiding the second. The link must run through
    THE RECEIPT: the second movement counts as UNBOOKED."""
    from core.retail import stock_books as sb
    po = _po(conn, lines=((10, 3.0),))
    r1, v1 = _receipt(conn, po_mod, po, [6])
    r2, v2 = _receipt(conn, po_mod, po, [4])
    _receipt_movement(conn, po, r1, 0, 6)
    _receipt_movement(conn, po, r2, 0, 4)
    assert sb.unbooked_movement_count(conn, CID) == 2, 'neither receipt is journalled yet'
    gl.post_po_receipt_for(conn, CID, BRANCH, _po_row(conn, po), r1, v1, CUR)
    assert sb.unbooked_movement_count(conn, CID) == 1, \
        "the first receipt is booked; the second one's missing journal must still show"
    gl.post_po_receipt_for(conn, CID, BRANCH, _po_row(conn, po), r2, v2, CUR)
    assert sb.unbooked_movement_count(conn, CID) == 0
