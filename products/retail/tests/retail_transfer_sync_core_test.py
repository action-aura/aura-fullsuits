"""Aura Retail -- W3b core: the pure rules that let a stock transfer be shared
between computers (core/retail/stock_transfers.py) and the receive-journal uid
(general_ledger.post_transfer_receive's `receipt_key`).

WHAT EACH GROUP GUARDS (and the mutation that must turn it RED):
  * movement uids: uuid5 replaced by uuid4 (or the namespace edited) -- two
    computers would each mint their own uid, the unique index would never
    collide and the same send/receipt would move the stock twice.
  * qty_canon / in-uid: `5` vs `5.0` vs `5.00` must be one receipt; `50` must
    not become '5E+1'; two different quantities must stay two movements.
  * transfer_rank: every pair of states in the table, plus the cancelled-before
    -send vs recalled split that the status alone cannot express.
  * receive journal uid: identical receipts collapse to ONE journal, different
    receipts both post (books identical on every computer), and the legacy
    behaviour (no key -> plain transfer id) is unchanged for old callers.

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


def test_lines_canon_is_order_independent_and_receipt_key_names_the_transfer():
    assert st.lines_canon([(I2, 3), (I1, 5.0)]) == f'{I1}=5;{I2}=3'
    assert st.lines_canon([(I1, 5), (I2, 3)]) == st.lines_canon([(I2, 3.0), (I1, '5.00')])
    assert st.receipt_key(T, [(I1, 5)]) == f'{T}:{I1}=5'
    assert st.receipt_key(T, [(I1, 5)]) != st.receipt_key(T, [(I1, 4)])


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


def test_receive_journal_uid_follows_the_receipt_key(conn, gl):
    tid = str(uuid.uuid4())
    _sent(conn, gl, tid)
    key = st.receipt_key(tid, [(I1, 4)])
    jid, _ = gl.post_transfer_receive(conn, CID, 2, tid, [(1, 4, 4, 2.0)], CUR, receipt_key=key)
    row = conn.execute("SELECT uid, source_uid FROM gl_journals WHERE id=?", (jid,)).fetchone()
    assert row['uid'] == gl.stock_journal_uid(key, 'transfer_receive')
    assert row['uid'] != gl.stock_journal_uid(tid, 'transfer_receive')
    assert row['source_uid'] == tid, 'the journal still names the transfer, not the key'


def test_identical_receipts_collapse_to_one_journal_different_ones_both_post(conn, gl):
    tid = str(uuid.uuid4())
    _sent(conn, gl, tid)
    k_full = st.receipt_key(tid, [(I1, 4)])
    k_short = st.receipt_key(tid, [(I1, 3)])
    a, _ = gl.post_transfer_receive(conn, CID, 2, tid, [(1, 4, 4, 2.0)], CUR, receipt_key=k_full)
    again, _ = gl.post_transfer_receive(conn, CID, 2, tid, [(1, 4, 4.0, 2.0)], CUR,
                                        receipt_key=st.receipt_key(tid, [(I1, 4.0)]))
    assert again == a, 'the same receipt on a second computer is the same journal'
    other, _ = gl.post_transfer_receive(conn, CID, 2, tid, [(1, 4, 3, 2.0)], CUR, receipt_key=k_short)
    assert other is not None and other != a, 'a different received quantity posts its own journal'
    assert len(_journals(conn, gl.stock_journal_uid(k_full, 'transfer_receive'))) == 1
    assert len(_journals(conn, gl.stock_journal_uid(k_short, 'transfer_receive'))) == 1


def test_without_a_key_the_receive_journal_keeps_the_legacy_transfer_uid(conn, gl):
    """What old journals (and callers) rely on: no key -> uid from the plain
    transfer id, exactly as before W3b."""
    tid = str(uuid.uuid4())
    _sent(conn, gl, tid)
    jid, _ = gl.post_transfer_receive(conn, CID, 2, tid, [(1, 4, 4, 2.0)], CUR)
    assert conn.execute("SELECT uid FROM gl_journals WHERE id=?", (jid,)).fetchone()['uid'] == \
        gl.stock_journal_uid(tid, 'transfer_receive')


def test_the_send_journal_uid_is_untouched_by_w3b(conn, gl):
    tid = str(uuid.uuid4())
    _sent(conn, gl, tid)
    assert gl.send_journal(conn, CID, tid)['uid'] == gl.stock_journal_uid(tid, 'transfer_send')
