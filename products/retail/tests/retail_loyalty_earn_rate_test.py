"""
Aura Retail -- the loyalty EARN RATE is a shop setting (LOYALTY-EARN).

Before: create_sale hardcoded `int(total / 10)`. A basket under 10 earned
nothing and the owner could not choose a rate. Now `loyalty_earn_spend`
(retail_settings, synced shop-wide like every retail_setting) = "spend this
much to earn 1 point"; '0' = earning off.

Proves: default 10 (100 -> 10 pts); spend 5 (100 -> 20); spend 0 -> 0 points,
no earn ledger row, no loyalty_entry queued; exactness via Decimal (0.300 at
spend 0.1 -> 3, float division gives 2); invalid values refused with
LOYALTY_EARN_SPEND_INVALID and nothing stored; the setting queues a
retail_setting sync event; a return after a rate change claws back the STORED
earned points, not a recomputation.

One pytest process per file (AURA_APP_DATA resolves at import time).
"""
import os
import shutil
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_loyalty_earn_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

import database.schema as sch  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    retail_product_lookup_nocase_test.py's own module docstring for why
#    nothing here is shared via a conftest.py) ──────────────────────────────

def _make_user(role, company_id, capabilities=None):
    """A real account with real capability rows -- matches
    retail_loyalty_redemption_test.py's own `_make_user`."""
    email = f"earnrate-{role}-{uuid.uuid4().hex[:10]}@test.local"
    # Same exemption, and same reasoning, as retail_loyalty_redemption_test.py:
    # a fixture password for a throwaway user this test creates. detect-secrets
    # flags any `password = "..."`; the pragma keeps the exemption visible in
    # the file rather than buried in a 221-entry baseline.
    password = "RetLoyaltyPW1"  # pragma: allowlist secret
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, "active"),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    if capabilities:
        for code, level in capabilities.items():
            conn.execute(
                "UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?",
                (level, user_id, code),
            )
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client


def _new_shop():
    """A fresh company with one logged-in ADMIN user (bypasses every
    capability gate) -- each test gets its OWN company so ledger balances
    and settings from one test never leak into another's assertions."""
    company_id = str(uuid.uuid4())
    admin = _make_user('admin', company_id)
    admin.get(f'{API}/settings/tax')  # forces _ensure_credit_schema/doc_sequences, matching sibling files
    return company_id, admin


@pytest.fixture
def shop():
    return _new_shop()


def _create_product(client, sell_price=100.0, tax_rate=0):
    tag = uuid.uuid4().hex[:8]
    payload = {
        'name': f'Return-loyalty item {tag}', 'sku': f'RETLOY-{tag}',
        'sell_price': sell_price, 'cost_price': 1.0, 'tax_rate': tax_rate,
        'initial_stock': 1000,
    }
    r = client.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_customer(client):
    payload = {'name': f'Return Loyalty Customer {uuid.uuid4().hex[:6]}'}
    r = client.post(f'{API}/customers', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _sale(client, items, **extra):
    payload = dict({'items': items, 'payment_method': 'cash',
                     'idempotency_key': str(uuid.uuid4())}, **extra)
    return client.post(f'{API}/sales', json=payload)


def _return(client, sale_id, items, reason='test return'):
    payload = {'sale_id': sale_id, 'items': items, 'reason': reason,
               'idempotency_key': str(uuid.uuid4())}
    return client.post(f'{API}/returns', json=payload)


def _set_loyalty_point_value(client, value):
    r = client.post(f'{API}/settings/credit', json={'loyalty_point_value': value})
    assert r.status_code == 200, r.get_json()


def _seed_loyalty_balance(company_id, customer_id, points):
    """Seeds a STARTING balance the way the real `_migrate_add_loyalty_ledger`
    backfill does: an 'opening' ledger row AND the matching
    `customers.loyalty_points` cache value, kept IN SYNC from the start --
    exactly like a genuine pre-existing balance would be (the backfill reads
    the cache and copies it into the ledger; it never leaves the two
    disagreeing). This file asserts on both the ledger sum AND the cache
    column, so both must start in sync for the cache assertions to mean
    anything -- a ledger-only write (retail_loyalty_redemption_test.py's own
    `_grant_ledger_points`, fine there because that file never asserts on
    the cache column) would leave the cache stuck at 0 while the ledger
    carried the seeded balance."""
    conn = sch.get_retail_conn()
    conn.execute(
        "INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,created_by) "
        "VALUES (?,?,?,?,'opening',?)",
        (str(uuid.uuid4()), company_id, customer_id, points, 'test-fixture'))
    conn.execute("UPDATE customers SET loyalty_points=? WHERE id=?", (points, customer_id))
    conn.commit()
    conn.close()


def _apply_ledger_and_cache_delta(company_id, customer_id, delta, entry_type='adjust'):
    """Writes a ledger row AND applies the identical delta to the
    `customers.loyalty_points` cache in the same call -- mirrors what every
    real mutation (create_sale's earn/redeem write, and this feature's own
    return reversal) always does to both columns together. Used to simulate
    a balance already moved by some OTHER, out-of-scope operation (e.g. a
    separate sale's redemption) before the return under test runs, without
    leaving the two columns artificially out of sync by construction."""
    conn = sch.get_retail_conn()
    conn.execute(
        "INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,created_by) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, customer_id, delta, entry_type, 'test-fixture'))
    conn.execute("UPDATE customers SET loyalty_points=loyalty_points+? WHERE id=?", (delta, customer_id))
    conn.commit()
    conn.close()


def _ledger_balance(company_id, customer_id):
    conn = sch.get_retail_conn()
    row = conn.execute(
        "SELECT COALESCE(SUM(points_delta),0) AS bal FROM loyalty_ledger WHERE company_id=? AND customer_id=?",
        (company_id, customer_id)).fetchone()
    conn.close()
    return row['bal']


def _ledger_rows(company_id, customer_id):
    conn = sch.get_retail_conn()
    rows = conn.execute(
        "SELECT * FROM loyalty_ledger WHERE company_id=? AND customer_id=? ORDER BY id",
        (company_id, customer_id)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _adjust_rows(company_id, customer_id):
    """Only the rows this feature's reversal could have written -- see
    `_grant_ledger_points`'s own docstring for why 'adjust' is otherwise
    unused anywhere in this file's fixtures."""
    return [r for r in _ledger_rows(company_id, customer_id) if r['entry_type'] == 'adjust']


def _company_ledger_row_count(company_id):
    conn = sch.get_retail_conn()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM loyalty_ledger WHERE company_id=?", (company_id,)
    ).fetchone()['n']
    conn.close()
    return n


def _customer_cache_points(customer_id):
    conn = sch.get_retail_conn()
    row = conn.execute("SELECT loyalty_points FROM customers WHERE id=?", (customer_id,)).fetchone()
    conn.close()
    return row['loyalty_points']


def _set_earn_spend(client, value):
    return client.post(f'{API}/settings/credit', json={'loyalty_earn_spend': value})


def _count(sql, params=()):
    conn = sch.get_retail_conn()
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _earn_rows(company_id):
    return _count("SELECT COUNT(*) FROM loyalty_ledger WHERE company_id=? AND entry_type='earn'", (company_id,))


def _loyalty_outbox():
    return _count("SELECT COUNT(*) FROM sync_outbox WHERE entity_type='loyalty_entry'")


def _stored_spend(client):
    return client.get(f'{API}/settings/credit').get_json()['data']['loyalty_earn_spend']


def test_default_spend_10_earns_10_on_100(shop):
    cid, admin = shop
    assert _stored_spend(admin) == '10'
    pid = _create_product(admin, sell_price=100.0)
    cust = _create_customer(admin)
    assert _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=cust, amount_paid=999999).status_code == 200
    assert _ledger_balance(cid, cust) == 10


def test_spend_5_earns_20_on_100(shop):
    cid, admin = shop
    assert _set_earn_spend(admin, '5').status_code == 200
    pid = _create_product(admin, sell_price=100.0)
    cust = _create_customer(admin)
    r = _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=cust, amount_paid=999999)
    assert r.status_code == 200, r.get_json()
    assert _ledger_balance(cid, cust) == 20


def test_small_basket_now_earns_at_a_low_spend(shop):
    cid, admin = shop
    assert _set_earn_spend(admin, '1').status_code == 200
    pid = _create_product(admin, sell_price=7.0)
    cust = _create_customer(admin)
    assert _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=cust, amount_paid=999999).status_code == 200
    assert _ledger_balance(cid, cust) == 7


def test_spend_0_turns_earning_off_no_row_no_queue(shop):
    cid, admin = shop
    assert _set_earn_spend(admin, '0').status_code == 200
    assert _stored_spend(admin) == '0'
    pid = _create_product(admin, sell_price=100.0)
    cust = _create_customer(admin)
    before = _loyalty_outbox()
    r = _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=cust, amount_paid=999999)
    assert r.status_code == 200, r.get_json()
    assert _earn_rows(cid) == 0
    assert _loyalty_outbox() == before
    assert _ledger_balance(cid, cust) == 0


def test_three_dp_total_is_exact_decimal_not_float(shop):
    cid, admin = shop
    assert _set_earn_spend(admin, '3').status_code == 200
    pid = _create_product(admin, sell_price=30.0)
    cust = _create_customer(admin)
    assert _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=cust, amount_paid=999999).status_code == 200
    assert _ledger_balance(cid, cust) == 10
    # The float trap: 0.3 // 0.1 == 2.0 and int(0.3 / 0.1) == 2 in floats.
    assert _set_earn_spend(admin, '0.1').status_code == 200
    pid2 = _create_product(admin, sell_price=0.3)
    cust2 = _create_customer(admin)
    assert _sale(admin, [{'product_id': pid2, 'quantity': 1}], customer_id=cust2, amount_paid=999999).status_code == 200
    assert _ledger_balance(cid, cust2) == 3, "0.300 at 0.1 per point must earn 3 (Decimal), not 2 (float)"


@pytest.mark.parametrize('bad', ['-1', 'abc', 'nan', 'inf', '', '1000001'])
def test_invalid_values_refused_and_stored_value_unchanged(shop, bad):
    _, admin = shop
    assert _set_earn_spend(admin, '5').status_code == 200
    r = _set_earn_spend(admin, bad)
    assert r.status_code == 400, r.get_json()
    assert r.get_json().get('code') == 'LOYALTY_EARN_SPEND_INVALID'
    assert _stored_spend(admin) == '5'


def test_setting_is_queued_as_retail_setting_sync_event(shop):
    _, admin = shop
    n = _count("SELECT COUNT(*) FROM sync_outbox WHERE entity_type='retail_setting'")
    assert _set_earn_spend(admin, '2.5').status_code == 200
    assert _count("SELECT COUNT(*) FROM sync_outbox WHERE entity_type='retail_setting'") == n + 1
    assert _stored_spend(admin) == '2.5'


def test_return_claws_back_stored_points_not_recomputed(shop):
    cid, admin = shop
    pid = _create_product(admin, sell_price=100.0)
    cust = _create_customer(admin)
    sale = _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=cust, amount_paid=999999).get_json()['data']
    assert _ledger_balance(cid, cust) == 10  # earned at the OLD rate (10)
    assert _set_earn_spend(admin, '5').status_code == 200  # rate doubles afterwards
    rr = _return(admin, sale['id'], [{'product_id': pid, 'quantity': 1}])
    assert rr.status_code == 200, rr.get_json()
    assert _ledger_balance(cid, cust) == 0, "claw back the 10 actually earned, not 20 at the new rate"
