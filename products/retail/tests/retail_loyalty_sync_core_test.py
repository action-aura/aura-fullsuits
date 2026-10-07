"""Aura Retail -- W2a core: the pieces loyalty-points sync stands on, proven
directly against a real migrated database (no Flask, no HTTP).

WHY THESE TESTS EXIST (W2a). loyalty_ledger rows used to stay on the computer
that wrote them. Sharing them needs two rules that must be identical on every
computer and in three different files (the API, the importer, the sync apply):

  * `opening_uid` -- a customer's 'opening' row has a uid that is a pure
    function of the customer, so each computer's own v27 opening row for the
    same customer COLLIDES on the unique uid index instead of doubling the
    balance once rows sync.
  * `refresh_points` -- `customers.loyalty_points` is a CACHE of
    SUM(points_delta); ONE rule refreshes it, and it never touches
    row_version (an accumulator must not look like a customer edit).

WHAT EACH TEST GUARDS (and the mutation that must turn it RED):
  * opening uid: uuid4 instead of uuid5; ignoring the customer id.
  * refresh: summing another customer's / another company's rows; adding
    instead of replacing (a replay would double the cache); bumping row_version.
  * balance: scoping dropped.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP_DIRS = []
CID = 48


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


@pytest.fixture()
def conn():
    tmp = tempfile.mkdtemp(prefix='aura-retail-loyalty-core-')
    _TMP_DIRS.append(tmp)
    os.environ.update({'AURA_APP_DATA': tmp, 'AURA_STANDALONE': '1', 'AURA_SITE_RELAY_ENABLED': '0'})
    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    c = sqlite3.connect(os.path.join(sch.SUBSYS_DIR, 'retail.db'), timeout=30)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


@pytest.fixture()
def loy():
    from core.retail import loyalty
    return loyalty


def _customer(conn, company=CID):
    cust = str(uuid.uuid4())
    conn.execute("INSERT INTO customers (id, company_id, name, loyalty_points) VALUES (?,?,?,0)",
                 (cust, company, 'c'))
    return cust


def _row(conn, cust, delta, kind='earn', company=CID):
    conn.execute("INSERT INTO loyalty_ledger (uid, company_id, customer_id, points_delta, entry_type) "
                 "VALUES (?,?,?,?,?)", (str(uuid.uuid4()), company, cust, delta, kind))


def _cache(conn, cust):
    return conn.execute("SELECT loyalty_points, row_version FROM customers WHERE id=?", (cust,)).fetchone()


def test_opening_uid_is_a_pure_function_of_the_customer(loy):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    assert loy.opening_uid(a) == loy.opening_uid(a)
    assert loy.opening_uid(a) != loy.opening_uid(b)
    # version 5 (name based), NOT random: two computers must agree with no talking
    assert uuid.UUID(loy.opening_uid(a)).version == 5


def test_opening_uid_is_pinned_to_a_literal_so_the_namespace_cannot_drift(loy):
    # NEVER CHANGE: a changed namespace makes each computer mint its own opening uid.
    cust = '00000000-0000-0000-0000-000000000001'
    assert loy.opening_uid(cust) == str(
        uuid.uuid5(uuid.UUID('b3a7d1c4-5e92-4f08-9a61-0c2d8e4f7b13'), f'{cust}:opening'))


def test_refresh_points_sets_the_cache_to_the_sum_of_that_customers_rows(conn, loy):
    me, other = _customer(conn), _customer(conn)
    _row(conn, me, 10)
    _row(conn, me, -4, 'redeem')
    _row(conn, other, 99)
    _row(conn, me, 1000, company=CID + 1)  # another company's row for the same customer id
    conn.commit()
    assert loy.refresh_points(conn, CID, me) == 6
    assert _cache(conn, me)['loyalty_points'] == 6
    assert _cache(conn, other)['loyalty_points'] == 0, 'refresh touches only the named customer'


def test_refresh_points_replaces_rather_than_adds_so_a_replay_cannot_double(conn, loy):
    me = _customer(conn)
    _row(conn, me, 10)
    loy.refresh_points(conn, CID, me)
    loy.refresh_points(conn, CID, me)
    assert _cache(conn, me)['loyalty_points'] == 10


def test_refresh_points_never_bumps_row_version(conn, loy):
    me = _customer(conn)
    before = _cache(conn, me)['row_version']
    _row(conn, me, 5)
    loy.refresh_points(conn, CID, me)
    assert _cache(conn, me)['row_version'] == before, 'the cache is not a customer edit; it must not sync as one'


def test_refresh_points_goes_back_to_zero_when_no_rows_remain(conn, loy):
    me = _customer(conn)
    conn.execute("UPDATE customers SET loyalty_points=50 WHERE id=?", (me,))
    assert loy.refresh_points(conn, CID, me) == 0
    assert _cache(conn, me)['loyalty_points'] == 0


def test_balance_is_the_scoped_sum(conn, loy):
    me, other = _customer(conn), _customer(conn)
    _row(conn, me, 7)
    _row(conn, me, -2, 'redeem')
    _row(conn, other, 50)
    _row(conn, me, 100, company=CID + 1)
    assert loy.balance(conn, CID, me) == 5
    assert loy.balance(conn, CID, 'nobody') == 0


def test_smaller_picks_the_lower_value_either_way_round(loy):
    assert loy.smaller(5, 3) == 3 and loy.smaller(3, 5) == 3 and loy.smaller(4, 4) == 4
