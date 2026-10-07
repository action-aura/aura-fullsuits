"""Aura Retail -- W3b core: the pure rules that let a stock transfer be shared
between computers (core/retail/stock_transfers.py) and the receive-journal uid
(general_ledger's per-line receive journal and cost-keyed send journal, W3b verify F1/F2).

WHAT EACH GROUP GUARDS (and the mutation that must turn it RED):
  * movement uids: uuid5 replaced by uuid4 (or the namespace edited) -- two
    computers would each mint their own uid, the unique index would never
    collide and the same send/receipt would move the stock twice.
  * qty_canon / in-uid: `5` vs `5.0` vs `5.00` must be one receipt; `50` must
    not become '5E+1'; two different quantities must stay two movements.
  * transfer_rank: every pair of states in the table, plus the cancelled-before
    -send vs recalled split that the status alone cannot express.
  * receive journal uid (W3b verify F2): ONE journal per line, keyed by (transfer, line,
    quantity) -- an agreeing line collapses, a differing line posts both.
  * send journals (W3b verify F1): keyed by the line costs; the winner is the journal
    keyed by the converged (smaller) costs; the loser is reversed once, with a uid every
    computer derives alike; nothing is reversed while the costs have not converged.

Run:
    pytest products/retail/tests/retail_transfer_sync_core_test.py -q
"""
import itertools
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

from core.retail import stock_transfers as st  # noqa: E402

CID = 78
CUR = 'JOD'
_TMP_DIRS = []
T = '11111111-1111-4111-8111-111111111111'
I1 = '22222222-2222-4222-8222-222222222222'
I2 = '33333333-3333-4333-8333-333333333333'


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


# -- movement uids ---------------------------------------------------------

def test_movement_uids_are_pure_functions_with_the_pinned_namespace():
    ns = uuid.UUID('6c1f0d52-8e7b-4a39-b5d4-2f9a73e8c1b6')
    assert st.out_movement_uid(T, I1) == str(uuid.uuid5(ns, f'{T}:{I1}:out'))
    assert st.recall_movement_uid(T, I1) == str(uuid.uuid5(ns, f'{T}:{I1}:recall'))
    assert st.in_movement_uid(T, I1, 5) == str(uuid.uuid5(ns, f'{T}:{I1}:in:5'))
    assert st.out_movement_uid(T, I1) == st.out_movement_uid(T, I1)


def test_out_recall_and_in_uids_never_collide_with_each_other_or_across_lines():
    uids = {st.out_movement_uid(T, I1), st.recall_movement_uid(T, I1), st.in_movement_uid(T, I1, 5),
            st.out_movement_uid(T, I2), st.recall_movement_uid(T, I2), st.in_movement_uid(T, I2, 5),
            st.out_movement_uid('44444444-4444-4444-8444-444444444444', I1)}
    assert len(uids) == 7


@pytest.mark.parametrize('a,b', [(5, 5.0), (5, '5.00'), (5, Decimal('5.000')), (50, 50.0),
                                 (0.5, '0.50'), (0, -0.0), (0, '0.0')])
def test_the_same_quantity_written_two_ways_is_one_receipt(a, b):
    assert st.qty_canon(a) == st.qty_canon(b)
    assert st.in_movement_uid(T, I1, a) == st.in_movement_uid(T, I1, b)


def test_different_quantities_are_different_movements_and_tens_do_not_go_scientific():
    assert st.in_movement_uid(T, I1, 5) != st.in_movement_uid(T, I1, 4)
    assert st.in_movement_uid(T, I1, 5) != st.in_movement_uid(T, I1, 50)
    assert st.qty_canon(50) == '50' and st.qty_canon(500.0) == '500'
    assert st.qty_canon(0.5) == '0.5' and st.qty_canon(0) == '0'


# W3b verify F2: `lines_canon` / `receipt_key` (one journal per RECEIPT) are gone -- the
# journal is per LINE now (`receive_line_key`). What their test could catch (a receipt key
# that depends on line order) no longer applies: nothing keys on a whole receipt.


# -- rank ------------------------------------------------------------------

_STATES = {
    'pending': ('pending', None),
    'cancelled_before_send': ('cancelled', None),
    'in_transit': ('in_transit', None),
    'recalled': ('cancelled', '2026-10-07 10:00:00'),
    'received': ('received', None),
}
_EXPECT = {'pending': 0, 'cancelled_before_send': 1, 'in_transit': 2, 'recalled': 3, 'received': 4}


def test_rank_table_is_exactly_the_decided_order():
    for name, (status, recalled_at) in _STATES.items():
        assert st.transfer_rank(status, recalled_at) == _EXPECT[name], name


def test_rank_orders_every_pair_strictly_and_a_recall_is_not_a_plain_cancel():
    for (n1, s1), (n2, s2) in itertools.permutations(_STATES.items(), 2):
        assert (st.transfer_rank(*s1) < st.transfer_rank(*s2)) == (_EXPECT[n1] < _EXPECT[n2]), (n1, n2)
        assert st.transfer_rank(*s1) != st.transfer_rank(*s2)
    # a received transfer outranks a recall in either order; send outranks a plain cancel
    assert st.transfer_rank('received') > st.transfer_rank('cancelled', 'x')
    assert st.transfer_rank('in_transit') > st.transfer_rank('cancelled', None)
    assert st.transfer_rank('cancelled', 'x') > st.transfer_rank('in_transit')
    # an empty-string recalled_at (a column that holds '') is not a recall
    assert st.transfer_rank('cancelled', '') == st.RANK_CANCELLED_BEFORE_SEND


def test_rank_refuses_a_status_it_does_not_know():
    with pytest.raises(ValueError):
        st.transfer_rank('shipped')


def test_earliest_is_set_once_and_never_lets_empty_replace_real():
    assert st.earliest(None, '2026-01-02') == '2026-01-02'
    assert st.earliest('2026-01-02', None) == '2026-01-02'
    assert st.earliest('', '2026-01-02') == '2026-01-02'
    assert st.earliest('2026-01-03', '2026-01-02') == '2026-01-02'
    assert st.earliest('2026-01-02', '2026-01-03') == '2026-01-02'
    assert st.earliest(None, None) is None


# -- receive journal uid (GL) ----------------------------------------------

def _install():
    tmp = tempfile.mkdtemp(prefix='aura-retail-w3b-core-')
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


def _journals(conn, uid):
    return conn.execute("SELECT id FROM gl_journals WHERE company_id=? AND uid=?", (CID, uid)).fetchall()


def _sent(conn, gl, tid, qty=4, cost=2.0):
    gl.post_transfer_send(conn, CID, 1, tid, [(1, qty, cost)], CUR)


def test_the_receive_line_uid_names_the_line_and_the_quantity(conn, gl):
    """W3b verify F2: one journal PER LINE. Replaces the per-receipt key test."""
    tid = str(uuid.uuid4())
    _sent(conn, gl, tid)
    jid, _ = gl.post_transfer_receive(conn, CID, 2, tid, [(I1, 4, 4, 2.0)], CUR)
    row = conn.execute("SELECT uid, source_uid FROM gl_journals WHERE id=?", (jid,)).fetchone()
    assert row['uid'] == gl.stock_journal_uid(st.receive_line_key(tid, I1, 4), 'transfer_receive_line')
    assert row['uid'] != gl.stock_journal_uid(tid, 'transfer_receive')
    assert row['source_uid'] == tid, 'the journal still names the transfer, not the key'


def test_identical_line_receipts_collapse_different_ones_both_post(conn, gl):
    tid = str(uuid.uuid4())
    _sent(conn, gl, tid)
    a, _ = gl.post_transfer_receive(conn, CID, 2, tid, [(I1, 4, 4, 2.0)], CUR)
    again, _ = gl.post_transfer_receive(conn, CID, 2, tid, [(I1, 4, 4.0, 2.0)], CUR)
    assert again == a, 'the same line counted the same on a second computer is the same journal'
    other, _ = gl.post_transfer_receive(conn, CID, 2, tid, [(I1, 4, 3, 2.0)], CUR)
    assert other is not None and other != a, 'a different received quantity posts its own journal'
    assert len(_journals(conn, gl.stock_journal_uid(st.receive_line_key(tid, I1, 4), 'transfer_receive_line'))) == 1
    assert len(_journals(conn, gl.stock_journal_uid(st.receive_line_key(tid, I1, 3), 'transfer_receive_line'))) == 1


def test_a_two_line_receipt_where_one_line_matches_books_that_line_once(conn, gl):
    """W3b verify F2 (the overlap finding): computer A receives (3, 2), computer
    B receives (3, 1). Line 1 agrees, so its movement and now its JOURNAL
    collapse; line 2 differs, so both post. Before the fix each receipt was one
    journal, both posted, and line 1's value was booked twice (1200 above the
    stock value). Mutation: key the journal per RECEIPT again and line 1 is
    counted twice."""
    tid = str(uuid.uuid4())
    gl.post_transfer_send(conn, CID, 1, tid, [(1, 3, 2.0), (2, 2, 2.0)], CUR)     # 1250 Dr 10
    gl.post_transfer_receive(conn, CID, 2, tid, [(I1, 3, 3, 2.0), (I2, 2, 2, 2.0)], CUR)   # A
    gl.post_transfer_receive(conn, CID, 2, tid, [(I1, 3, 3, 2.0), (I2, 2, 1, 2.0)], CUR)   # B
    tot = {r['code']: Decimal(str(r['bal'])) for r in conn.execute(
        "SELECT a.code, SUM(l.debit)-SUM(l.credit) AS bal FROM gl_journal_lines l "
        "JOIN gl_accounts a ON a.id=l.account_id GROUP BY a.code")}
    # Inventory: line1 once (6) + line2 both counts (4 + 2) = 12, NOT 6 + 6 + 4 + 2 = 18.
    assert tot['1200'] == Decimal('12') - Decimal('10'), tot       # send Cr 10, receipts Dr 12
    assert len(conn.execute(
        "SELECT id FROM gl_journals WHERE source_uid=? AND source_type='stock_transfer'", (tid,)).fetchall()) == 4


def test_two_different_lines_counted_the_same_each_post_their_own_journal(conn, gl):
    """The other half of F2: the journal collapses per LINE, not per quantity. Two
    lines of 2 received as 2 are two journals; keying on the quantity alone would
    drop the second line's books. Mutation: replace the line key with a constant."""
    tid = str(uuid.uuid4())
    gl.post_transfer_send(conn, CID, 1, tid, [(1, 2, 2.0), (2, 2, 2.0)], CUR)
    gl.post_transfer_receive(conn, CID, 2, tid, [(I1, 2, 2, 2.0), (I2, 2, 2, 2.0)], CUR)
    n = conn.execute("SELECT COUNT(*) FROM gl_journals WHERE source_uid=? AND source_type='stock_transfer'",
                     (tid,)).fetchone()[0]
    assert n == 3, 'one send journal + one receive journal PER LINE'


def test_a_receipt_with_no_send_journal_posts_nothing(conn, gl):
    assert gl.post_transfer_receive(conn, CID, 2, str(uuid.uuid4()), [(I1, 4, 4, 2.0)], CUR) == (None, {})


def test_the_legacy_send_journal_uid_is_still_found(conn, gl):
    """A transfer posted without a costs key (pre-fix, or an old caller) keeps the
    plain-tid uid and `send_journal` still returns it."""
    tid = str(uuid.uuid4())
    _sent(conn, gl, tid)
    assert gl.send_journal(conn, CID, tid)['uid'] == gl.stock_journal_uid(tid, 'transfer_send')


# -- W3b verify F1: sends keyed by cost, the smaller cost wins, loser reversed --

def test_costs_canon_and_send_key_are_deterministic_and_cost_sensitive():
    assert st.costs_canon([(I2, 3), (I1, 5.0)]) == f'{I1}=5;{I2}=3'
    assert st.costs_canon([(I1, 5), (I2, 3)]) == st.costs_canon([(I2, 3.0), (I1, '5.00')])
    assert st.send_key(T, [(I1, 5)]) == f'{T}:{I1}=5'
    assert st.send_key(T, [(I1, 5)]) != st.send_key(T, [(I1, 2)])


@pytest.mark.parametrize('a,b,want', [(5, 2, 2), (2, 5, 2), (None, 3, 3), (3, None, 3),
                                      (3, 3, 3), ('4', 5.0, '4'), (0, 5, 0)])
def test_smaller_is_the_deterministic_tie_break(a, b, want):
    assert st.smaller(a, b) == want


def _transfer_with_line(conn, tid, item, cost, qty=4):
    conn.execute("INSERT OR IGNORE INTO stock_transfers (id,company_id,source_branch_id,destination_branch_id,status) "
                 "VALUES (?,?,1,2,'in_transit')", (tid, CID))
    conn.execute("INSERT OR IGNORE INTO stock_transfer_items (id,transfer_id,product_id,quantity_sent,unit_cost) "
                 "VALUES (?,?,1,?,?)", (item, tid, qty, cost))


def _send_at(conn, gl, tid, item, cost, qty=4):
    gl.post_transfer_send(conn, CID, 1, tid, [(1, qty, cost)], CUR, costs_key=st.send_key(tid, [(item, cost)]))


def _reversal_of(conn, uid):
    return conn.execute(
        "SELECT r.id, r.uid FROM gl_journals r JOIN gl_journals o ON o.id=r.reversed_journal_id "
        "WHERE o.company_id=? AND o.uid=?", (CID, uid)).fetchall()


def test_two_sends_at_different_costs_post_two_journals_and_the_winner_is_the_converged_cost(conn, gl):
    tid = str(uuid.uuid4())
    _transfer_with_line(conn, tid, I1, 2.0)          # the line has converged to the SMALLER cost
    _send_at(conn, gl, tid, I1, 5.0)                 # A's send
    _send_at(conn, gl, tid, I1, 2.0)                 # B's send
    u5 = gl.stock_journal_uid(st.send_key(tid, [(I1, 5.0)]), 'transfer_send')
    u2 = gl.stock_journal_uid(st.send_key(tid, [(I1, 2.0)]), 'transfer_send')
    assert u5 != u2 and len(_journals(conn, u5)) == 1 and len(_journals(conn, u2)) == 1
    win = gl.send_journal(conn, CID, tid)
    assert win['uid'] == u2 and win['amount'] == Decimal('8.000'), 'send_journal returns the WINNER'


def test_reconcile_reverses_only_the_loser_once_and_the_uid_is_deterministic(conn, gl):
    tid = str(uuid.uuid4())
    _transfer_with_line(conn, tid, I1, 2.0)
    _send_at(conn, gl, tid, I1, 5.0)
    _send_at(conn, gl, tid, I1, 2.0)
    u5 = gl.stock_journal_uid(st.send_key(tid, [(I1, 5.0)]), 'transfer_send')
    u2 = gl.stock_journal_uid(st.send_key(tid, [(I1, 2.0)]), 'transfer_send')
    first = gl.reconcile_transfer_send_duplicates(conn, CID, tid)
    assert len(first) == 1
    assert gl.reconcile_transfer_send_duplicates(conn, CID, tid) == [], 'idempotent'
    [rev] = _reversal_of(conn, u5)
    assert rev['uid'] == gl.reversal_uid(u5), 'every computer mints the SAME reversal uid'
    assert _reversal_of(conn, u2) == [], 'the winner is never reversed'
    net = conn.execute(
        "SELECT SUM(l.debit)-SUM(l.credit) FROM gl_journal_lines l JOIN gl_accounts a ON a.id=l.account_id "
        "WHERE a.code='1250'").fetchone()[0]
    assert Decimal(str(net)) == Decimal('8.000'), 'the transit account carries the send value ONCE'


def test_reconcile_waits_while_the_line_cost_has_not_converged(conn, gl):
    """The transient the safety rule exists for: computer A holds its own send at 5
    and the line still says 5; B's cheaper journal (2) has arrived but the line
    update has not. 'Winner' by line cost would be A's own journal and the
    cheaper one would be reversed -- the wrong one. A loser CHEAPER than the
    winner means not converged, so nothing is reversed yet."""
    tid = str(uuid.uuid4())
    _transfer_with_line(conn, tid, I1, 5.0)          # line not yet lowered to 2
    _send_at(conn, gl, tid, I1, 5.0)
    _send_at(conn, gl, tid, I1, 2.0)
    assert gl.reconcile_transfer_send_duplicates(conn, CID, tid) == []
    assert conn.execute("SELECT COUNT(*) FROM gl_journals WHERE source_type='reversal'").fetchone()[0] == 0
    # the line then converges to 2 and the next pass reverses the 5-journal
    conn.execute("UPDATE stock_transfer_items SET unit_cost=2.0 WHERE id=?", (I1,))
    assert len(gl.reconcile_transfer_send_duplicates(conn, CID, tid)) == 1


def test_reconcile_does_nothing_for_a_single_send_or_a_legacy_transfer(conn, gl):
    tid = str(uuid.uuid4())
    _transfer_with_line(conn, tid, I1, 2.0)
    _send_at(conn, gl, tid, I1, 2.0)
    assert gl.reconcile_transfer_send_duplicates(conn, CID, tid) == []
    legacy = str(uuid.uuid4())
    _transfer_with_line(conn, legacy, I2, 2.0)
    _sent(conn, gl, legacy)                          # plain-tid uid, no costs key
    assert gl.reconcile_transfer_send_duplicates(conn, CID, legacy) == []
    assert gl.send_journal(conn, CID, legacy)['uid'] == gl.stock_journal_uid(legacy, 'transfer_send')
    assert (CID, tid) not in gl.transfers_with_duplicate_sends(conn)


def test_receive_after_a_double_send_clears_the_transit_account_to_zero(conn, gl):
    """The principle for a clash, on one computer's books: after the loser is
    reversed and the line is received, 1250 is 0."""
    tid = str(uuid.uuid4())
    _transfer_with_line(conn, tid, I1, 2.0)
    _send_at(conn, gl, tid, I1, 5.0)
    _send_at(conn, gl, tid, I1, 2.0)
    gl.reconcile_transfer_send_duplicates(conn, CID, tid)
    gl.post_transfer_receive(conn, CID, 2, tid, [(I1, 4, 4, 2.0)], CUR)
    net = conn.execute(
        "SELECT SUM(l.debit)-SUM(l.credit) FROM gl_journal_lines l JOIN gl_accounts a ON a.id=l.account_id "
        "WHERE a.code='1250'").fetchone()[0]
    assert Decimal(str(net)) == Decimal('0')
