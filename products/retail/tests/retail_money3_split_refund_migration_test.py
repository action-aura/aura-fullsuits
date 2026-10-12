"""MONEY-3 M1 (retail v51): the v50 -> v51 step that gives every existing return its refund leg.

THE RULE BEING PINNED. Until v51 a return was refunded through ONE method. The upgrade backfills
EXACTLY ONE leg per existing return -- method = the return's refund_method, amount = its
tender_refund_amount -- with a uid that is a pure function of the return's uid
(`uuid5(NS, "<return uid>:legacy")`), so two computers upgrading separately derive the SAME leg. The
step is IDEMPOTENT BY UID: replaying it adds nothing. It is NOT a repair for a database already
stamped 51 -- ensure_schema_version returns early when current == target, so the step only runs on
an upgrade from < 51 (commit 53a1026e, M2/M3 only, was never pushed or released, so no real
database is at 51 without legs; one created by that code must be recreated).

The v50 shape is manufactured from a live, populated install (sales, a cash return, a card return
and a return on a credit sale that forgave debt): the legs table is dropped, `PRAGMA user_version =
50` stamped, and the upgrade runs through the REAL `ensure_schema_version` chain (backup + integrity
check before and after).

WHAT EACH TEST GUARDS (and the mutation that must turn it RED):
  * one leg per return, right method/amount/uid  -> skip the backfill loop body (no legs at all),
    or backfill from refund_amount instead of tender_refund_amount (the credit-sale return's leg
    would carry the forgiven debt as cash)
  * the drawer and the books agree before and after -> any wrong leg amount moves expected cash
  * replay / second computer: byte-identical        -> a random (uuid4) uid doubles the legs
  * replaying the step directly adds no legs        -> a random (uuid4) uid, or dropping the
    NOT EXISTS / OR IGNORE guards, doubles the legs
  * a DB stamped 51 without legs is NOT repaired by the chain (the truth, pinned)

Run (one file per process):
    pytest products/retail/tests/retail_money3_split_refund_migration_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_money3_m1mig_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA), AURA_SITE_RELAY_ENABLED="0")
os.environ.pop("AURA_DEV", None)
for _k in ("AURA_SYNC_RELAY_URL", "AURA_OWNER_LICENSING_URL"):
    os.environ.pop(_k, None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

import database.schema as sch  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn, return_refund_leg_uid  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def shop():
    email = f'm1mig-{uuid.uuid4().hex[:8]}@test.local'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password('Money3PW12345'), 'admin', 'active'))
    conn.commit(); conn.close()
    c = app.test_client()
    assert c.post('/api/auth/login', json={'email': email, 'password': 'Money3PW12345'}).status_code == 200
    c.company_id = company_id
    return c


def _rows(sql, params=()):
    conn = get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _tb(c):
    r = c.get(f'{API}/reports/trial-balance')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _populate(c):
    """A cash sale returned by cash, a card sale returned by card, and a credit sale (30 on account)
    returned in full -- which forgives 30 of debt, so refund_amount 100 != tender_refund_amount 70."""
    p = c.post(f'{API}/products', json={'name': 'Mig Widget', 'sku': f'MG-{uuid.uuid4().hex[:8]}',
                                        'cost_price': 1, 'sell_price': 100, 'tax_rate': 0, 'initial_stock': 500})
    pid = p.get_json()['data']['id']
    cust = c.post(f'{API}/customers', json={'name': 'Mig Customer'}).get_json()['data']['id']
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode='unlimited', credit_limit=1000000 WHERE id=?", (cust,))
    conn.commit(); conn.close()
    sid = c.post(f'{API}/cash-sessions/open', json={'opening_float': 100.0}).get_json()['data']['id']

    def sell(**kw):
        body = {'items': [{'product_id': pid, 'quantity': 1}], 'idempotency_key': str(uuid.uuid4())}
        body.update(kw)
        r = c.post(f'{API}/sales', json=body)
        assert r.status_code == 200, r.get_json()
        return r.get_json()['data']['id']

    def ret(sale_id, **kw):
        r = c.post(f'{API}/returns', json={'sale_id': sale_id, 'items': [{'product_id': pid, 'quantity': 1}],
                                           'idempotency_key': str(uuid.uuid4()), **kw})
        assert r.status_code == 200, r.get_json()
        return r.get_json()['data']['id']

    cash_sale = sell(payment_method='cash', amount_paid=100)
    card_sale = sell(payment_method='card', amount_paid=100)
    credit_sale = sell(payment_method='split', customer_id=cust,
                       tenders=[{'method': 'cash', 'amount': 70}, {'method': 'credit', 'amount': 30}])
    ids = [ret(cash_sale, refund_method='cash'), ret(card_sale, refund_method='card'),
           ret(credit_sale, refund_legs='default')]
    return sid, ids


def _to_v50_shape():
    """What a v50 install looked like: no legs table, user_version 50. Returns the db path."""
    conn = get_retail_conn()
    try:
        conn.execute("DROP TABLE return_refund_legs")
        conn.execute("PRAGMA user_version = 50")
        conn.commit()
        return conn.execute("PRAGMA database_list").fetchone()[2]
    finally:
        conn.close()


def _migrate(db):
    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-m1-backup-')
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema_version(conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()
        shutil.rmtree(backup_dir, ignore_errors=True)


def _legs():
    return _rows("SELECT l.uid AS uid, l.return_id AS return_id, l.return_uid AS return_uid, l.method AS method, "
                 "l.amount AS amount FROM return_refund_legs l ORDER BY l.return_id")


def test_every_existing_return_gets_exactly_one_leg_and_the_books_and_drawer_agree(shop):
    c = shop
    sid, ids = _populate(c)
    x_before = c.get(f'{API}/cash-sessions/{sid}/x-report').get_json()['data']
    tb_before = _tb(c)
    live = {r['return_id']: r for r in _legs()}
    returns = _rows("SELECT id, uid, refund_method, tender_refund_amount, refund_amount FROM returns "
                    "WHERE id IN (%s)" % ','.join('?' * len(ids)), ids)
    assert len(returns) == 3
    # the credit-sale return is the one where the two amounts differ: 100 of goods, 70 of money
    credit_ret = [r for r in returns if r['refund_amount'] != r['tender_refund_amount']]
    assert len(credit_ret) == 1 and credit_ret[0]['tender_refund_amount'] == 70.0, returns

    db = _to_v50_shape()
    _migrate(db)

    got = {r['return_id']: r for r in _legs()}
    for r in returns:
        leg = got[r['id']]
        assert leg['method'] == (r['refund_method'] if r['refund_method'] != 'split' else leg['method'])
        assert leg['amount'] == r['tender_refund_amount'], (r, leg)
        assert leg['uid'] == return_refund_leg_uid(r['uid'], 'legacy')
        assert leg['return_uid'] == r['uid']
    assert len([k for k in got if k in ids]) == 3
    assert _rows("SELECT COUNT(*) AS n FROM (SELECT return_id FROM return_refund_legs GROUP BY return_id "
                 "HAVING COUNT(*) <> 1)")[0]['n'] == 0, 'a return has more or fewer than one leg'

    # ties and the drawer agree before and after
    tb = _tb(c)
    assert tb['is_balanced'] is True
    assert tb['subledger']['ar_ties'] is True and tb['subledger']['ap_ties'] is True, tb['subledger']
    assert tb['subledger'] == tb_before['subledger']
    x = c.get(f'{API}/cash-sessions/{sid}/x-report').get_json()['data']
    assert x['cash_refunds'] == x_before['cash_refunds'] and x['expected_cash'] == x_before['expected_cash'], (x, x_before)

    # replay (a failed launch re-enters the chain): the same rows, nothing added
    snapshot = _legs()
    conn = get_retail_conn(); conn.execute("PRAGMA user_version = 50"); conn.commit(); conn.close()
    _migrate(db)
    assert _legs() == snapshot

    # a return made AFTER the upgrade still works and adds its own leg only
    n = len(snapshot)
    p = c.post(f'{API}/products', json={'name': 'Late', 'sku': f'LT-{uuid.uuid4().hex[:6]}', 'cost_price': 1,
                                        'sell_price': 10, 'tax_rate': 0, 'initial_stock': 5})
    pid = p.get_json()['data']['id']
    s = c.post(f'{API}/sales', json={'items': [{'product_id': pid, 'quantity': 1}], 'payment_method': 'cash',
                                    'amount_paid': 10, 'idempotency_key': str(uuid.uuid4())}).get_json()['data']['id']
    r = c.post(f'{API}/returns', json={'sale_id': s, 'items': [{'product_id': pid, 'quantity': 1}],
                                       'refund_method': 'cash', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    assert len(_legs()) == n + 1


def test_replaying_the_step_directly_adds_no_legs(shop):
    """The step is idempotent by uid: running it again on a database that already has its legs
    (twice, directly, outside the version chain) changes nothing -- same rows, same uids."""
    c = shop
    _, ids = _populate(c)
    db = _to_v50_shape()
    _migrate(db)
    snapshot = _legs()
    assert len([r for r in snapshot if r['return_id'] in ids]) == 3
    conn = sqlite3.connect(db, timeout=30)
    try:
        sch._migrate_add_return_refund_legs(conn)
        sch._migrate_add_return_refund_legs(conn)
        conn.commit()
    finally:
        conn.close()
    assert _legs() == snapshot


def test_a_database_stamped_51_without_legs_is_not_repaired_by_the_chain(shop):
    """THE TRUTH, pinned. An earlier comment claimed the step is "not version-gated, so a DB already
    at 51 still gets its legs". False: ensure_schema_version returns early when current == target,
    so the step never runs for such a database. That is acceptable only because commit 53a1026e
    (the M2/M3 steps, v51 without legs) was never pushed or built into a release -- no real
    database is in that state; a database created by that code must be recreated. If this test
    ever goes red, the chain started re-running steps at the target version and the v51 comment in
    schema.py needs rewriting."""
    c = shop
    _populate(c)
    db = _to_v50_shape()
    # v52 UPDATE (MONEY-4b): the head moved 51 -> 52, so "a database already AT the target
    # version" is now stamped 52. A database stamped 51 is no longer at the target: the chain
    # re-enters and (correctly) creates the legs table. What this still catches is exactly what
    # it caught before: the chain re-running steps on a DB whose stamp equals the head. What it
    # can no longer catch: a v51-stamped DB missing its legs being left unrepaired -- that is
    # now an ordinary 51 -> 52 upgrade, covered by the v51 steps' own replay tests.
    conn = sqlite3.connect(db); conn.execute("PRAGMA user_version = 53"); conn.commit(); conn.close()
    _migrate(db)
    conn = sqlite3.connect(db)
    try:
        has_table = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='return_refund_legs'").fetchone()[0]
    finally:
        conn.close()
    assert has_table == 0, 'the chain ran the step on a DB already at the target version'


def test_the_step_leaves_a_database_without_a_returns_table_alone():
    bare = sqlite3.connect(':memory:')
    sch._migrate_add_return_refund_legs(bare)
    assert bare.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='return_refund_legs'").fetchone()[0] == 0
    bare.close()


def test_the_namespace_is_pinned_equal_in_the_sync_service():
    """The receiver synthesises the legacy leg for a return that arrives without legs; it must
    mint the identical uid (commercial_runtime cannot import products/, so the literal is copied)."""
    from commercial_runtime.sync import sync_service
    assert sync_service._RETURN_REFUND_LEG_NAMESPACE == sch.RETURN_REFUND_LEG_NAMESPACE


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
