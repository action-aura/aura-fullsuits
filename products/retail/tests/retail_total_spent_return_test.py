"""Aura Retail -- `customers.total_spent` must be netted by a return.

`total_spent` is a lifetime accumulator on the customer row. create_sale is
its ONLY writer (retail_api.py, the same UPDATE that bumps `loyalty_points`):

    UPDATE customers SET total_spent=total_spent+?, loyalty_points=loyalty_points+?-?

create_return adjusts the customer's `loyalty_points` on a refund (the
earn-clawback / redeem-giveback block) and adjusts `credit_balance` through
`_adjust_credit` -- and never touched `total_spent`. No comment anywhere marked
that asymmetry as deliberate; the only documentation on the column explains why
it is device-local and not synced, not why a refund would leave it alone.

SO A FULLY REFUNDED SALE COUNTED FOREVER. Buy 500, return all of it, and the
customer still reads "Total Spent 500". That figure is not decorative: the
Customers list is ordered by it (`ORDER BY c.total_spent DESC` in retail_api),
so a customer who returned everything outranks a genuine repeat buyer, and the
customer detail screen prints it as a money figure.

WHAT IS SUBTRACTED. The return's own `refund` -- the tax-inclusive value of the
goods actually coming back, recomputed per line by create_return, which is the
same basis `sales.total` used when create_sale added it. Floored at 0: a
lifetime "total spent" below zero is not a number that means anything, and this
is a display/ranking accumulator, not a ledger.

NOT SYNCED AND NOT ROW-VERSION-BUMPING, exactly like the loyalty accumulator
beside it -- see create_sale's own comment and
`test_loyalty_accumulator_sale_does_not_bump_row_version_or_emit`. Bumping here
would let a return ringing on till A reject a genuine name/phone correction
made on till B as stale.

Self-contained bootstrap; no shared conftest.py. CRITICAL: exactly ONE pytest
process per file (AUDIT-010).

Run:
    pytest products/retail/tests/retail_total_spent_return_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_totalspent_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(
    AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
    AURA_SITE_RELAY_ENABLED="0",
)
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin(price=250.0, stock=50):
    email = f"tspent-{uuid.uuid4().hex[:10]}@test.local"
    password = "TotalSpentPW1"
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()

    rconn = get_retail_conn()
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)
    ).fetchone()[0]

    def _mk(sku):
        rconn.execute(
            "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,?,?,?,?,0)",
            (str(uuid.uuid4()), company_id, sku, sku, price / 2, price))
        pid = rconn.execute(
            "SELECT id FROM products WHERE company_id=? AND sku=?", (company_id, sku)).fetchone()[0]
        rconn.execute(
            "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
            (company_id, pid, branch_id, stock))
        return pid

    pid_a = _mk(f'TS-A-{uuid.uuid4().hex[:6]}')
    pid_b = _mk(f'TS-B-{uuid.uuid4().hex[:6]}')
    rconn.commit()
    rconn.close()

    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    client.get(f"{API}/settings/tax")

    import random as _random
    dconn = get_retail_conn()
    for _doc in ('sale', 'return'):
        dconn.execute(
            "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
            (company_id, _doc, _random.randint(1, 5_000_000)))
    dconn.commit()
    dconn.close()
    return client, company_id, pid_a, pid_b


def _make_customer(cid, name='Total Spent Customer'):
    rconn = get_retail_conn()
    customer_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO customers (id,company_id,name,credit_mode,credit_limit,credit_balance,total_spent) "
        "VALUES (?,?,?,'unlimited',0,0,0)",
        (customer_id, cid, name))
    rconn.commit()
    rconn.close()
    return customer_id


def _customer_row(cid, customer_id):
    rconn = get_retail_conn()
    row = rconn.execute(
        "SELECT total_spent, row_version FROM customers WHERE id=? AND company_id=?",
        (customer_id, cid)).fetchone()
    rconn.close()
    return row


def _sell(client, customer_id, pids, amount_paid, payment_method='cash'):
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': p, 'quantity': 1} for p in pids],
        'customer_id': customer_id, 'amount_paid': amount_paid,
        'payment_method': payment_method, 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _return(client, sale_id, pids):
    r = client.post(f'{API}/returns', json={
        'sale_id': sale_id, 'items': [{'product_id': p, 'quantity': 1} for p in pids],
        'reason': 'total_spent test', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


# ── the defect ────────────────────────────────────────────────────────────────

def test_a_fully_returned_sale_stops_counting_as_money_spent():
    """Buy 500 (two 250 lines), return both. The customer spent nothing, and
    the figure the Customers list is ORDERED by must say so."""
    client, cid, pid_a, pid_b = _make_admin(price=250.0)
    customer_id = _make_customer(cid)

    sale = _sell(client, customer_id, [pid_a, pid_b], amount_paid=500.0)
    assert sale['total'] == 500.0, sale
    assert _customer_row(cid, customer_id)['total_spent'] == 500.0, (
        "precondition: create_sale records the spend")

    _return(client, sale['id'], [pid_a, pid_b])

    assert _customer_row(cid, customer_id)['total_spent'] == 0.0, (
        "a fully refunded sale must stop counting; got "
        f"{_customer_row(cid, customer_id)['total_spent']!r}")


def test_a_partial_return_nets_only_what_came_back():
    """Return one 250 line of a 500 sale: 250 genuinely stayed spent."""
    client, cid, pid_a, pid_b = _make_admin(price=250.0)
    customer_id = _make_customer(cid)

    sale = _sell(client, customer_id, [pid_a, pid_b], amount_paid=500.0)
    _return(client, sale['id'], [pid_a])

    assert _customer_row(cid, customer_id)['total_spent'] == 250.0, (
        f"got {_customer_row(cid, customer_id)['total_spent']!r}")


def test_total_spent_never_goes_negative():
    """A lifetime "total spent" below zero is not a number that means
    anything. Floored, because this is a display and ranking accumulator, not
    a ledger -- the ledger figures (credit_balance, the settlement columns)
    are the ones allowed to go negative, and they have their own tests."""
    client, cid, pid_a, pid_b = _make_admin(price=250.0)
    customer_id = _make_customer(cid)

    sale = _sell(client, customer_id, [pid_a, pid_b], amount_paid=500.0)
    # Manufacture the pathological state directly: a customer whose recorded
    # lifetime spend is already lower than the sale about to be returned.
    rconn = get_retail_conn()
    rconn.execute("UPDATE customers SET total_spent=100 WHERE id=?", (customer_id,))
    rconn.commit()
    rconn.close()

    _return(client, sale['id'], [pid_a, pid_b])

    assert _customer_row(cid, customer_id)['total_spent'] == 0.0, (
        f"floored at zero, got {_customer_row(cid, customer_id)['total_spent']!r}")


# ── the allow half ────────────────────────────────────────────────────────────

def test_netting_total_spent_does_not_bump_row_version():
    """THE RULE THIS ACCUMULATOR SHARES WITH loyalty_points, and the reason
    create_sale's own comment spells it out: this UPDATE must not bump
    `row_version` and must not emit a sync event. If it did, a return rung on
    till A would advance the customer's version, and a genuine name/phone
    correction made on till B would later be rejected as stale -- a shop could
    become unable to fix a customer's phone number from any device but the one
    that sells to them least."""
    client, cid, pid_a, pid_b = _make_admin(price=250.0)
    customer_id = _make_customer(cid)

    sale = _sell(client, customer_id, [pid_a, pid_b], amount_paid=500.0)
    before = _customer_row(cid, customer_id)['row_version']
    _return(client, sale['id'], [pid_a, pid_b])
    after = _customer_row(cid, customer_id)['row_version']

    assert after == before, (
        f"an accumulator write must not bump row_version: {before!r} -> {after!r}")


def test_a_walk_in_return_still_works():
    """No customer on the sale: the whole block is skipped, quietly, the same
    way create_sale's own `if customer_id:` guard skips it."""
    client, cid, pid_a, pid_b = _make_admin(price=250.0)

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid_a, 'quantity': 1}],
        'amount_paid': 250.0, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']

    ret = client.post(f'{API}/returns', json={
        'sale_id': sale['id'], 'items': [{'product_id': pid_a, 'quantity': 1}],
        'reason': 'walk-in', 'idempotency_key': str(uuid.uuid4()),
    })
    assert ret.status_code == 200, ret.get_json()
