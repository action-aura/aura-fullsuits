"""Aura Retail -- STOCK-BOOKS core: the ledger helpers that put stock events in
the books, proven directly against a real migrated database (no Flask, no
HTTP -- same level as retail_gl_posting_test.py). The route-level versions
(receive/cancel/send/recall/adjust through the real endpoints) are
retail_stock_books_routes_test.py's job; this file proves the arithmetic and
the once-only/uid guarantees they stand on.

WHAT EACH GROUP GUARDS (and the mutation that must turn it RED):
  * PO order -> 1260, receipt -> 1200 (T1/T2/T3): post_purchase_order debiting
    1200 again, or post_po_receipt posting for a legacy order.
  * once-only + deterministic uid (idempotency, uid tests): `_post_once`
    removed, `stock_journal_uid` returning uuid4.
  * adjustments / opening entries (T5/T6): valued at the 3-level cost chain,
    never at cost_price alone; never source_type 'opening_balance'.
  * transfers (T7): the receipt credits 1250 by the SEND journal's own amount.
  * the Inventory tie (T9): 'not_opened' detection, rounding vs differs.
  * a cancelled PO leaves the supplier balance (T10).

Run:
    pytest products/retail/tests/retail_stock_books_core_test.py -q
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
# A company that is NOT the seeded demo company 1: init_retail() seeds twelve
# demo products and 124 movements for company 1, which would swamp every stock
# figure asserted here. Company 77 starts genuinely empty.
CID = 77
BRANCH = 1
CUR = 'JOD'


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _install():
    tmp = tempfile.mkdtemp(prefix='aura-retail-stock-books-')
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp
    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    conn = sqlite3.connect(os.path.join(sch.SUBSYS_DIR, 'retail.db'), timeout=30)
    conn.row_factory = sqlite3.Row
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
def sb():
    from core.retail import stock_books as _sb
    return _sb


# ── helpers ──────────────────────────────────────────────────────────────

def _product(conn, cost=2.0, avg=None, branch_avg=None, qty=0.0, sku=None):
    """A product plus its branch balance row. `avg`/`branch_avg` default to
    `cost` (a product whose cost is known everywhere) -- pass 0 to model a
    product whose only cost is the manual cost_price."""
    avg = cost if avg is None else avg
    branch_avg = cost if branch_avg is None else branch_avg
    pid = str(uuid.uuid4())   # products.id is a TEXT uuid, not a rowid
    conn.execute(
        "INSERT INTO products (id, company_id, name, sku, cost_price, sell_price, avg_cost, status) "
        "VALUES (?,?,?,?,?,?,?, 'active')",
        (pid, CID, 'Widget', sku or uuid.uuid4().hex[:8], cost, 5.0, avg))
    conn.execute(
        "INSERT INTO inventory_balances (company_id, product_id, branch_id, quantity_on_hand, avg_cost) "
        "VALUES (?,?,?,?,?)", (CID, pid, BRANCH, qty, branch_avg))
    return pid


def _move(conn, pid, mtype, qty, cost, reference='ref'):
    """A stock movement plus the balance change it implies; returns its uid."""
    uid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO inventory_movements (company_id, product_id, branch_id, movement_type, "
        "quantity, unit_cost, reference, uid) VALUES (?,?,?,?,?,?,?,?)",
        (CID, pid, BRANCH, mtype, qty, cost, reference, uid))
    conn.execute(
        "UPDATE inventory_balances SET quantity_on_hand = quantity_on_hand + ? "
        "WHERE company_id=? AND product_id=? AND branch_id=?", (qty, CID, pid, BRANCH))
    return uid


def _tb(conn, gl, as_of=None):
    return gl.trial_balance(conn, CID, as_of)


def _bal(conn, gl, code):
    return Decimal(str(gl._account_balance_in(_tb(conn, gl), code)))


def _journal_count(conn):
    return conn.execute("SELECT COUNT(*) FROM gl_journals WHERE company_id=?", (CID,)).fetchone()[0]


def _lines(conn, journal_id):
    """{(code, side): amount} for one journal."""
    out = {}
    for r in conn.execute(
            "SELECT a.code, l.debit, l.credit FROM gl_journal_lines l "
            "JOIN gl_accounts a ON a.id=l.account_id WHERE l.journal_id=?", (journal_id,)):
        if r['debit']:
            out[(r['code'], 'dr')] = Decimal(str(r['debit']))
        if r['credit']:
            out[(r['code'], 'cr')] = Decimal(str(r['credit']))
    return out


def _tie(conn, gl):
    return gl.inventory_tie(conn, CID, _tb(conn, gl))


D = Decimal


# ── chart ────────────────────────────────────────────────────────────────

def test_chart_seeds_the_four_stock_accounts_flat_with_frozen_types(conn, gl):
    gl.ensure_default_chart(conn, CID)
    rows = {r['code']: r for r in conn.execute(
        "SELECT code, name, account_type, normal_balance, parent_id FROM gl_accounts WHERE company_id=?", (CID,))}
    expected = {
        '1250': ('Stock in Transit', 'asset', 'debit'),
        '1260': ('Goods Ordered Not Received', 'asset', 'debit'),
        '3900': ('Opening Balance Equity', 'equity', 'credit'),
        '5910': ('Shrinkage and Adjustments', 'expense', 'debit'),
    }
    for code, (name, typ, normal) in expected.items():
        r = rows[code]
        assert (r['name'], r['account_type'], r['normal_balance']) == (name, typ, normal)
        assert r['parent_id'] is None, 'new codes are flat (the 4000/4900 parent is the one legacy exception)'
    assert (gl.ACCOUNT_STOCK_IN_TRANSIT, gl.ACCOUNT_GOODS_ORDERED,
            gl.ACCOUNT_OPENING_EQUITY, gl.ACCOUNT_SHRINKAGE) == ('1250', '1260', '3900', '5910')


def test_a_company_seeded_before_the_wave_gets_the_new_codes_on_next_post(conn, gl):
    """A pre-wave company has the old ten accounts only; the next posting
    must complete the chart (ensure_default_chart compares the code SET)."""
    gl.ensure_default_chart(conn, CID)
    conn.execute("DELETE FROM gl_accounts WHERE company_id=? AND code IN ('1250','1260','3900','5910')", (CID,))
    uid = str(uuid.uuid4())
    jid = gl.post_stock_adjustment(conn, CID, BRANCH, uid, -1, 2.0, 'r', CUR)
    assert jid is not None
    assert _bal(conn, gl, '5910') == D('2.000')


# ── uid + once-only (M14, M15) ───────────────────────────────────────────

def test_stock_journal_uid_is_a_pure_function_of_key_and_kind(gl):
    a = gl.stock_journal_uid('K', 'po_receipt')
    assert a == gl.stock_journal_uid('K', 'po_receipt')
    assert a == str(uuid.uuid5(gl._GL_STOCK_NAMESPACE, 'K:po_receipt'))
    assert a != gl.stock_journal_uid('K', 'transfer_send')
    assert a != gl.stock_journal_uid('K2', 'po_receipt')


def test_posted_journal_carries_the_deterministic_uid_and_source_uid(conn, gl):
    """M14: stock_journal_uid returning uuid4 makes two computers mint
    different uids for the same event, so sync would book it twice."""
    mv = str(uuid.uuid4())
    jid = gl.post_stock_adjustment(conn, CID, BRANCH, mv, 3, 2.0, 'r', CUR)
    row = conn.execute("SELECT uid, source_uid, source_type FROM gl_journals WHERE id=?", (jid,)).fetchone()
    assert row['uid'] == gl.stock_journal_uid(mv, 'stock_adjustment')
    assert row['source_uid'] == mv
    assert row['source_type'] == 'stock_adjustment'


def test_every_stock_helper_posts_once_per_event(conn, gl):
    """M15: with `_post_once` replaced by a bare post_journal the second call
    would hit the UNIQUE uid index (or double-book); the contract is the
    SAME journal id back and exactly one journal."""
    mv = str(uuid.uuid4())
    first = gl.post_stock_adjustment(conn, CID, BRANCH, mv, -3, 2.0, 'r', CUR)
    n = _journal_count(conn)
    second = gl.post_stock_adjustment(conn, CID, BRANCH, mv, -3, 2.0, 'r', CUR)
    assert second == first and _journal_count(conn) == n
    assert conn.execute("SELECT COUNT(*) FROM gl_journal_lines WHERE journal_id=?", (first,)).fetchone()[0] == 2
    ov = str(uuid.uuid4())
    o1 = gl.post_opening_entry(conn, CID, BRANCH, ov, 5, 1.0, 'r', CUR)
    n = _journal_count(conn)
    assert gl.post_opening_entry(conn, CID, BRANCH, ov, 5, 1.0, 'r', CUR) == o1 and _journal_count(conn) == n
    t = str(uuid.uuid4())
    s1 = gl.post_transfer_send(conn, CID, BRANCH, t, [(1, 4, 2.0)], CUR)
    n = _journal_count(conn)
    assert gl.post_transfer_send(conn, CID, BRANCH, t, [(1, 4, 2.0)], CUR) == s1 and _journal_count(conn) == n
    r1, _ = gl.post_transfer_receive(conn, CID, 2, t, [(1, 4, 4, 2.0)], CUR)
    n = _journal_count(conn)
    r2, _ = gl.post_transfer_receive(conn, CID, 2, t, [(1, 4, 4, 2.0)], CUR)
    assert r2 == r1 and _journal_count(conn) == n


def test_zero_amount_posts_nothing(conn, gl):
    assert gl.post_stock_adjustment(conn, CID, BRANCH, str(uuid.uuid4()), 5, 0, 'r', CUR) is None
    assert gl.post_opening_entry(conn, CID, BRANCH, str(uuid.uuid4()), 5, 0, 'r', CUR) is None
    assert gl.post_transfer_send(conn, CID, BRANCH, str(uuid.uuid4()), [(1, 5, 0)], CUR) is None
    assert _journal_count(conn) == 0


# ── T1 / T2 / T3: purchase orders ────────────────────────────────────────

def test_t1_order_goes_to_1260_and_receipt_moves_it_to_1200(conn, gl):
    """M1: post_purchase_order debiting 1200 turns the first assertion red;
    a receipt that posts nothing turns the second red."""
    gl.post_purchase_order(conn, CID, BRANCH, 7, 'PO-7', CUR, 0, D('40.000'))
    assert _bal(conn, gl, '1260') == D('40')
    assert _bal(conn, gl, '1200') == D('0')
    assert _bal(conn, gl, '2000') == D('40')
    order = gl.po_order_journal(conn, CID, 7)
    assert (order['debit_code'], order['debit_amount']) == ('1260', D('40.000'))

    rid = gl.post_po_receipt(conn, CID, BRANCH, 7, 'PO-7', CUR)
    assert rid is not None
    assert _bal(conn, gl, '1200') == D('40')
    assert _bal(conn, gl, '1260') == D('0')
    row = conn.execute("SELECT uid, source_uid, source_type FROM gl_journals WHERE id=?", (rid,)).fetchone()
    assert row['uid'] == gl.stock_journal_uid(order['uid'], 'po_receipt')
    assert row['source_uid'] == order['uid'] and row['source_type'] == 'po_receipt'

    # deny half: receiving again posts nothing new
    n = _journal_count(conn)
    assert gl.post_po_receipt(conn, CID, BRANCH, 7, 'PO-7', CUR) == rid
    assert _journal_count(conn) == n
    assert _bal(conn, gl, '1200') == D('40')


def test_t1_receipt_moves_exactly_what_the_order_booked_not_qty_times_cost(conn, gl):
    """A supplier-less PO with 10 paid of 40 books only 10 (the accounted
    part); the receipt must move 10, never 40."""
    gl.post_purchase_order(conn, CID, BRANCH, 8, 'PO-8', CUR, D('10.000'), 0)
    gl.post_po_receipt(conn, CID, BRANCH, 8, 'PO-8', CUR)
    assert _bal(conn, gl, '1200') == D('10')
    assert _bal(conn, gl, '1260') == D('0')


def test_t2_legacy_order_on_1200_is_not_booked_twice_by_the_receipt(conn, gl):
    """M3: a receipt that posts for a legacy order double-counts the stock."""
    gl.post_journal(conn, CID, BRANCH, '2026-10-01', 'purchase_order', 9, CUR, [
        {'account_code': gl.ACCOUNT_INVENTORY, 'debit': 40},
        {'account_code': gl.ACCOUNT_AP, 'credit': 40}])
    assert gl.po_order_journal(conn, CID, 9)['debit_code'] == '1200'
    assert gl.post_po_receipt(conn, CID, BRANCH, 9, 'PO-9', CUR) is None
    assert _journal_count(conn) == 1
    assert _bal(conn, gl, '1200') == D('40') and _bal(conn, gl, '1260') == D('0')


def test_a_po_with_no_order_journal_posts_nothing_on_receipt(conn, gl):
    assert gl.po_order_journal(conn, CID, 404) is None
    assert gl.post_po_receipt(conn, CID, BRANCH, 404, 'PO-404', CUR) is None


def test_t3_reversing_the_order_journal_empties_1260_and_ap(conn, gl):
    gl.post_purchase_order(conn, CID, BRANCH, 11, 'PO-11', CUR, 0, D('40.000'))
    order = gl.po_order_journal(conn, CID, 11)
    rev = gl.reverse_journal(conn, CID, order['id'])
    assert conn.execute("SELECT uid FROM gl_journals WHERE id=?", (rev,)).fetchone()['uid'] == \
        gl.reversal_uid(order['uid'])
    assert _bal(conn, gl, '1260') == D('0') and _bal(conn, gl, '2000') == D('0')
    n = _journal_count(conn)
    assert gl.reverse_journal(conn, CID, order['id']) == rev and _journal_count(conn) == n


# ── T5: adjustments ──────────────────────────────────────────────────────

def test_t5_adjustments_post_against_5910_and_the_books_track_the_shelf(conn, gl):
    pid = _product(conn, cost=2.0)
    mv = _move(conn, pid, 'opening_stock', 10, 2.0)
    j = gl.post_opening_entry(conn, CID, BRANCH, mv, 10, 2.0, 'open', CUR)
    assert _lines(conn, j) == {('1200', 'dr'): D('20.000'), ('3900', 'cr'): D('20.000')}

    mv = _move(conn, pid, 'stock_out', -3, 2.0)
    j = gl.post_stock_adjustment(conn, CID, BRANCH, mv, -3, 2.0, 'count', CUR)
    assert _lines(conn, j) == {('5910', 'dr'): D('6.000'), ('1200', 'cr'): D('6.000')}
    assert _bal(conn, gl, '1200') == D('14')

    mv = _move(conn, pid, 'stock_in', 1, 2.0)
    j = gl.post_stock_adjustment(conn, CID, BRANCH, mv, 1, 2.0, 'found', CUR)
    assert _lines(conn, j) == {('1200', 'dr'): D('2.000'), ('5910', 'cr'): D('2.000')}
    assert _bal(conn, gl, '1200') == D('16') and _bal(conn, gl, '5910') == D('4')

    # sell 2 @ 5.000, cost 2.000 -> COGS 4.000, books 12 == 6 units x 2
    _move(conn, pid, 'sale_out', -2, 2.0)
    gl.post_cogs(conn, CID, BRANCH, 'sale', 1, 'S-1', CUR, D('4.000'))
    assert _bal(conn, gl, '1200') == D('12')
    tb = _tb(conn, gl)
    assert tb['is_balanced']
    tie = gl.inventory_tie(conn, CID, tb)
    assert (tie['inventory_gl'], tie['stock_value'], tie['status']) == (12.0, 12.0, 'ties')


def test_t5_unit_cost_follows_the_branch_average_not_the_manual_cost_price(conn, sb):
    """The branch figure beats the company average beats cost_price. M9
    (valuing at cost_price) would book a -1 adjustment at 2.000, not 3.000."""
    pid = _product(conn, cost=2.0, avg=2.5, branch_avg=3.0, qty=10)
    assert sb.effective_unit_cost(conn, CID, pid, BRANCH, CUR) == D('3.0')
    conn.execute("UPDATE inventory_balances SET avg_cost=0 WHERE product_id=?", (pid,))
    assert sb.effective_unit_cost(conn, CID, pid, BRANCH, CUR) == D('2.5')
    conn.execute("UPDATE products SET avg_cost=0 WHERE id=?", (pid,))
    assert sb.effective_unit_cost(conn, CID, pid, BRANCH, CUR) == D('2.0')
    conn.execute("UPDATE products SET cost_price=0 WHERE id=?", (pid,))
    assert sb.effective_unit_cost(conn, CID, pid, BRANCH, CUR) == D('0')
    assert sb.effective_unit_cost(conn, CID, 99999, BRANCH, CUR) == D('0')


def test_t5_adjustment_at_the_effective_cost_books_the_branch_figure(conn, gl, sb):
    pid = _product(conn, cost=2.0, avg=2.0, branch_avg=3.0, qty=10)
    cost = sb.effective_unit_cost(conn, CID, pid, BRANCH, CUR)
    j = gl.post_stock_adjustment(conn, CID, BRANCH, str(uuid.uuid4()), -1, cost, 'x', CUR)
    assert _lines(conn, j) == {('5910', 'dr'): D('3.000'), ('1200', 'cr'): D('3.000')}


# ── T6: opening entries ──────────────────────────────────────────────────

def test_t6_opening_entry_at_a_three_decimal_cost_and_later_cost_edit(conn, gl):
    pid = _product(conn, cost=0.396)
    mv = _move(conn, pid, 'opening_stock', 4, 0.396)
    j = gl.post_opening_entry(conn, CID, BRANCH, mv, 4, 0.396, 'open', CUR)
    assert _lines(conn, j)[('1200', 'dr')] == D('1.584')
    _move(conn, pid, 'sale_out', -2, 0.396)
    gl.post_cogs(conn, CID, BRANCH, 'sale', 1, 'S-1', CUR, D('0.792'))
    assert _bal(conn, gl, '1200') == D('0.792')
    # the owner retypes the manual cost: the stock stays valued at its
    # moving average, so the books still tie (M11 -- not setting avg_cost at
    # entry -- would let this value jump to 18.000)
    conn.execute("UPDATE products SET cost_price=9 WHERE id=?", (pid,))
    tie = _tie(conn, gl)
    assert (tie['stock_value'], tie['inventory_gl'], tie['status']) == (0.792, 0.792, 'ties')
    # never the v38 backfill's source_type
    assert conn.execute(
        "SELECT COUNT(*) FROM gl_journals WHERE source_type='opening_balance'").fetchone()[0] == 0


def test_t6_stock_with_no_cost_posts_no_journal_and_is_not_opened(conn, gl):
    """M16: without not_opened detection a cost-less stock entry would read as
    'differs' (red) -- missing history reported as an error."""
    pid = _product(conn, cost=0, avg=0, branch_avg=0)
    mv = _move(conn, pid, 'opening_stock', 5, 0)
    assert gl.post_opening_entry(conn, CID, BRANCH, mv, 5, 0, 'open', CUR) is None
    assert _journal_count(conn) == 0
    tie = _tie(conn, gl)
    assert tie['status'] == 'not_opened' and tie['unbooked_movements'] == 1


# ── T7: transfers ────────────────────────────────────────────────────────

def _opened(conn, gl, qty=10, cost=2.0):
    pid = _product(conn, cost=cost)
    mv = _move(conn, pid, 'opening_stock', qty, cost)
    gl.post_opening_entry(conn, CID, BRANCH, mv, qty, cost, 'open', CUR)
    return pid


def test_t7_send_receive_with_a_shortage_writes_it_off_to_5910(conn, gl):
    """M12 (crediting 1250 only for received units) leaves 1250 at 2 here."""
    pid = _opened(conn, gl)
    t = str(uuid.uuid4())
    gl.post_transfer_send(conn, CID, BRANCH, t, [(pid, 4, 2.0)], CUR)
    assert (_bal(conn, gl, '1250'), _bal(conn, gl, '1200')) == (D('8'), D('12'))
    jid, off = gl.post_transfer_receive(conn, CID, 2, t, [(pid, 4, 3, 2.0)], CUR)
    assert _lines(conn, jid) == {('1200', 'dr'): D('6.000'), ('5910', 'dr'): D('2.000'),
                                 ('1250', 'cr'): D('8.000')}
    assert off == {pid: D('2.000')}
    assert (_bal(conn, gl, '1250'), _bal(conn, gl, '1200')) == (D('0'), D('18'))
    assert _bal(conn, gl, '5910') == D('2')
    assert _tb(conn, gl)['is_balanced']


def test_t7_over_receipt_credits_5910(conn, gl):
    pid = _opened(conn, gl)
    t = str(uuid.uuid4())
    gl.post_transfer_send(conn, CID, BRANCH, t, [(pid, 4, 2.0)], CUR)
    jid, off = gl.post_transfer_receive(conn, CID, 2, t, [(pid, 4, 5, 2.0)], CUR)
    assert _lines(conn, jid) == {('1200', 'dr'): D('10.000'), ('1250', 'cr'): D('8.000'),
                                 ('5910', 'cr'): D('2.000')}
    assert off == {pid: D('-2.000')}
    assert _bal(conn, gl, '1250') == D('0')


def test_t7_legacy_in_transit_transfer_receive_posts_nothing(conn, gl):
    """No send journal (sent before the wave): nothing to clear, and 1250
    must never go negative."""
    pid = _opened(conn, gl)
    n = _journal_count(conn)
    assert gl.send_journal(conn, CID, str(uuid.uuid4())) is None
    assert gl.post_transfer_receive(conn, CID, 2, str(uuid.uuid4()), [(pid, 4, 4, 2.0)], CUR) == (None, {})
    assert _journal_count(conn) == n and _bal(conn, gl, '1250') == D('0')


def test_t7_recall_is_the_reversal_of_the_send_journal(conn, gl):
    pid = _opened(conn, gl)
    t = str(uuid.uuid4())
    gl.post_transfer_send(conn, CID, BRANCH, t, [(pid, 4, 2.0)], CUR)
    send = gl.send_journal(conn, CID, t)
    assert send['amount'] == D('8.000') and send['uid'] == gl.stock_journal_uid(t, 'transfer_send')
    rev = gl.reverse_journal(conn, CID, send['id'])
    assert conn.execute("SELECT uid FROM gl_journals WHERE id=?", (rev,)).fetchone()['uid'] == \
        gl.reversal_uid(send['uid'])
    assert (_bal(conn, gl, '1250'), _bal(conn, gl, '1200')) == (D('0'), D('20'))


def test_t7_receipt_credits_exactly_what_the_send_debited_despite_rounding(conn, gl):
    """M13: recomputing the 1250 credit from the received lines gives 0.333
    against a 0.334 debit, leaving a permanent one-quantum residue."""
    lines = [(1, 0.5, 0.333), (2, 0.5, 0.333)]
    t = str(uuid.uuid4())
    gl.post_transfer_send(conn, CID, BRANCH, t, lines, CUR)
    assert gl.send_journal(conn, CID, t)['amount'] == D('0.334')   # 2 x quantize(0.1665)
    gl.post_transfer_receive(conn, CID, 2, t, [(1, 0.5, 0.5, 0.333), (2, 0.5, 0.5, 0.333)], CUR)
    assert _bal(conn, gl, '1250') == D('0')


# ── T9: the Inventory tie ────────────────────────────────────────────────

def test_t9_rounding_then_differs_then_not_applicable(conn, gl):
    """3 @ 1.000 then 3 @ 1.001 -> moving average 1.001 -> shelf 6.006 vs
    books 6.003: a 0.003 gap the per-unit rounding allowance (6 x 0.0005)
    explains -> 'rounding'. A stray 1.000 into 1200 is beyond it -> 'differs'."""
    pid = _product(conn, cost=1.001)
    m1 = _move(conn, pid, 'opening_stock', 3, 1.0)
    m2 = _move(conn, pid, 'opening_stock', 3, 1.001)
    gl.post_opening_entry(conn, CID, BRANCH, m1, 3, 1.0, 'a', CUR)
    gl.post_opening_entry(conn, CID, BRANCH, m2, 3, 1.001, 'b', CUR)
    tie = _tie(conn, gl)
    assert (tie['inventory_gl'], tie['stock_value']) == (6.003, 6.006)
    assert tie['status'] == 'rounding' and tie['unbooked_movements'] == 0

    gl.post_journal(conn, CID, BRANCH, '2026-10-01', 'manual', None, CUR, [
        {'account_code': '1200', 'debit': 1.0}, {'account_code': '3000', 'credit': 1.0}])
    assert _tie(conn, gl)['status'] == 'differs'

    # a journal dated after as_of -> the comparison is not applicable
    tb = gl.trial_balance(conn, CID, '2000-01-01')
    out = gl.inventory_tie(conn, CID, tb)
    assert out['applicable'] is False and out['status'] == 'not_applicable'


def test_t9_a_booked_purchase_in_is_not_unbooked_but_an_unmatched_one_is(conn, gl, sb):
    pid = _product(conn, cost=4.0)
    gl.post_purchase_order(conn, CID, BRANCH, 21, 'PO-21', CUR, 0, D('40.000'))
    _move(conn, pid, 'purchase_in', 10, 4.0, reference='PO-21')
    assert sb.unbooked_movement_count(conn, CID) == 0
    _move(conn, pid, 'purchase_in', 1, 4.0, reference='PO-NO-JOURNAL')
    assert sb.unbooked_movement_count(conn, CID) == 1


def test_t9_a_movement_with_no_uid_can_never_be_booked(conn, sb):
    pid = _product(conn)
    conn.execute(
        "INSERT INTO inventory_movements (company_id, product_id, branch_id, movement_type, quantity, unit_cost) "
        "VALUES (?,?,?,'stock_in',1,2.0)", (CID, pid, BRANCH))
    assert sb.unbooked_movement_count(conn, CID) == 1
