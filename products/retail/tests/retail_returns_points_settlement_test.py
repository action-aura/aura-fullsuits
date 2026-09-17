"""
Aura Retail -- returns settlement, points/AR interaction (DEFECT 1) and
JOD tender-precision (DEFECT 2) regression suite.

THE BUGS THIS PINS SHUT

DEFECT 1: `create_sale` records `sales.total` PRE-points (the invoice stays
whole -- see that function's own "THE CASH TRAP" comment) but tracks what the
customer actually still owed via `amount_due_after_points = total -
points_redeemed_amount`, and `balance_due = amount_due_after_points - paid`
(api/retail_api.py's create_sale, ~line 6264/6278). `create_return` used to
recompute `original_balance_due` as bare `total - amount_paid`, silently
omitting the points term -- inventing `points_redeemed_amount` worth of
phantom AR headroom that create_sale never recorded. A full return of a
points-funded sale then had that headroom to hand out as `store_credit`
conjured from nothing, or -- if the same customer separately owed money on an
UNRELATED sale -- as `ar_forgiven` against that unrelated debt, writing off a
real receivable the returned sale never created.

DEFECT 2: `create_sale`'s own retained-cash `_record_payment(...)` call (its
`net_received` write, ~line 6686) used to omit the `currency` argument,
quantizing to a hardcoded 2 decimal places regardless of the shop's real
currency. On JOD (3dp, 1000 fils) this rounded a real tender like 4.007 to
4.01 on the way into the `payments` table -- 3 fils CREATED. `create_return`'s
tender cap (`tender_collected`, which sums exactly that column) then read
back more cash than the drawer ever actually took, and the AR side of the
same return was short by the identical 3 fils, leaving the customer
permanently owing a residue after a FULL return.

Run (ONE FILE PER PROCESS -- AUDIT-010, AURA_APP_DATA resolves at import
time):
    py -3.14 -m pytest products/retail/tests/retail_returns_points_settlement_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]        # products/retail
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent                    # aura-fullsuits
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_returns_points_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
# AURA_SITE_RELAY_ENABLED="0": documented kill switch
# (config.py::site_relay_should_start). Without it, a licensed WINDOWS
# install (seeded below) elects itself the LAN site-relay hub and starts
# four background threads plus a TLS listener -- see
# retail_einvoicing_regression_test.py's own comment on this exact
# interaction for the full story.
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
                  AURA_SITE_RELAY_ENABLED="0")
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

def _make_user(role, company_id):
    email = f"retpts-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "ReturnsPointsPW1"  # pragma: allowlist secret
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
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client


def _new_shop():
    """A fresh company with one logged-in ADMIN user (bypasses every
    capability gate) -- each test gets its OWN company so ledger balances
    and credit_balance from one test never leak into another's assertions."""
    company_id = str(uuid.uuid4())
    admin = _make_user('admin', company_id)
    admin.get(f'{API}/settings/tax')  # forces _ensure_credit_schema/doc_sequences
    return company_id, admin


@pytest.fixture
def shop():
    return _new_shop()


def _create_product(client, sell_price=100.0, tax_rate=0):
    tag = uuid.uuid4().hex[:8]
    payload = {
        'name': f'Returns/points test item {tag}', 'sku': f'RPS-{tag}',
        'sell_price': sell_price, 'cost_price': 1.0, 'tax_rate': tax_rate,
        'initial_stock': 1000,
    }
    r = client.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_customer(client):
    payload = {'name': f'Returns/points customer {uuid.uuid4().hex[:6]}'}
    r = client.post(f'{API}/customers', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _set_loyalty_point_value(client, value):
    r = client.post(f'{API}/settings/credit', json={'loyalty_point_value': value})
    assert r.status_code == 200, r.get_json()


def _allow_credit(customer_id):
    """Default customer credit_mode is 'none' (schema.py) -- a credit sale
    against it is refused with 400 'This customer is not allowed to buy on
    credit.' before it ever reaches the money math this file tests. Direct
    DB write, matching retail_returns_wave0_test.py's own
    `_make_credit_customer` convention, since there is no dedicated route
    for this in the tested surface."""
    conn = sch.get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode='unlimited' WHERE id=?", (customer_id,))
    conn.commit()
    conn.close()


def _grant_ledger_points(company_id, customer_id, points):
    """Writes an immutable ledger row directly -- same shape a real goodwill
    grant would write -- so a test's starting balance is exact and
    independent of the earn formula (matches
    retail_loyalty_redemption_test.py's own `_grant_ledger_points`)."""
    conn = sch.get_retail_conn()
    conn.execute(
        "INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,created_by) "
        "VALUES (?,?,?,?,'adjust',?)",
        (str(uuid.uuid4()), company_id, customer_id, points, 'test-fixture'))
    conn.commit()
    conn.close()


def _ledger_balance(company_id, customer_id):
    conn = sch.get_retail_conn()
    row = conn.execute(
        "SELECT COALESCE(SUM(points_delta),0) AS bal FROM loyalty_ledger WHERE company_id=? AND customer_id=?",
        (company_id, customer_id)).fetchone()
    conn.close()
    return row['bal']


def _credit_balance(company_id, customer_id):
    """No GET /customers/<id> route (only PATCH/DELETE) -- read the real
    column directly, matching retail_returns_wave0_test.py's own
    `_credit_balance`."""
    conn = sch.get_retail_conn()
    bal = conn.execute(
        "SELECT credit_balance FROM customers WHERE id=? AND company_id=?", (customer_id, company_id)
    ).fetchone()[0]
    conn.close()
    return bal


def _sale(client, items, **extra):
    """No default `amount_paid` -- matches
    retail_loyalty_redemption_test.py's own `_sale`: several cases below
    depend on OMITTING it entirely so create_sale falls back to
    `amount_due_after_points` rather than the sale's full `total`."""
    payload = dict({'items': items, 'payment_method': 'cash',
                     'idempotency_key': str(uuid.uuid4())}, **extra)
    return client.post(f'{API}/sales', json=payload)


def _return(client, sale_id, pid, quantity, **extra):
    payload = dict({
        'sale_id': sale_id,
        'items': [{'product_id': pid, 'quantity': quantity}],
        'reason': 'test',
        'idempotency_key': str(uuid.uuid4()),
    }, **extra)
    return client.post(f'{API}/returns', json=payload)


# ── 1. Points sale (100 total, 30 points, 70 cash), full return ─────────────

def test_points_sale_full_return_no_phantom_store_credit(shop):
    """MEASURED (before this file's fix): store_credit_amount=30.0,
    credit_balance=-30.0 -- the customer got 70 cash + 30 points back +
    30 phantom store credit, on top of the points. After the fix, the
    full 70 tendered comes back as tender_refund and nothing else --
    the points-covered 30 was never a debt in the first place.

    Mutation that must turn this red again: reintroduce `original_
    balance_due = total - amount_paid` (dropping the points_redeemed_amount
    subtraction) -- store_credit_amount goes back to 30.0 and credit_balance
    back to -30.0.
    """
    cid, admin = shop
    pid = _create_product(admin, sell_price=100.0, tax_rate=0)
    customer_id = _create_customer(admin)
    _set_loyalty_point_value(admin, 1)
    _grant_ledger_points(cid, customer_id, 50)

    sale_resp = _sale(admin, [{'product_id': pid, 'quantity': 1}],
                       customer_id=customer_id, points_redeemed=30, amount_paid=70.0)
    assert sale_resp.status_code == 200, sale_resp.get_json()
    sale = sale_resp.get_json()['data']
    assert sale['total'] == 100.0, sale
    assert sale['points_redeemed_amount'] == 30.0, sale
    assert sale['balance_due'] == 0.0, "sanity: this sale must be fully settled at sale time"

    r = _return(admin, sale['id'], pid, quantity=1)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['refund_amount'] == 100.0, data
    assert data['tender_refund_amount'] == 70.0, \
        f"expected the full 70 tendered back as tender_refund, got {data['tender_refund_amount']}"
    assert data['ar_forgiven_amount'] == 0.0, data
    assert data['store_credit_amount'] == 0.0, \
        f"store_credit must be exactly 0 -- the points-covered 30 was never a debt, got {data['store_credit_amount']}"
    assert _credit_balance(cid, customer_id) == 0.0, \
        f"credit_balance must be exactly 0, got {_credit_balance(cid, customer_id)}"
    assert _ledger_balance(cid, customer_id) == 50, \
        "a full return must restore the pre-sale points balance (earn clawed back, redeem given back)"


# ── 2. Sale paid 100% in points, full return ─────────────────────────────────

def test_fully_points_paid_sale_full_return_no_phantom_store_credit(shop):
    """MEASURED (before this file's fix): store_credit_amount=100.0,
    credit_balance=-100.0, on top of the 100 points returned -- pure
    manufacture, since the customer tendered no cash and owed nothing.

    Mutation that must turn this red again: same as test 1 -- drop the
    points_redeemed_amount subtraction from original_balance_due.
    """
    cid, admin = shop
    pid = _create_product(admin, sell_price=100.0, tax_rate=0)
    customer_id = _create_customer(admin)
    _set_loyalty_point_value(admin, 1)
    _grant_ledger_points(cid, customer_id, 100)

    sale_resp = _sale(admin, [{'product_id': pid, 'quantity': 1}],
                       customer_id=customer_id, points_redeemed=100)
    assert sale_resp.status_code == 200, sale_resp.get_json()
    sale = sale_resp.get_json()['data']
    assert sale['total'] == 100.0, sale
    assert sale['points_redeemed_amount'] == 100.0, sale
    assert sale['amount_paid'] == 0.0, sale
    assert sale['balance_due'] == 0.0, "sanity: fully covered by points, no debt"

    r = _return(admin, sale['id'], pid, quantity=1)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['tender_refund_amount'] == 0.0, data
    assert data['ar_forgiven_amount'] == 0.0, data
    assert data['store_credit_amount'] == 0.0, \
        f"store_credit must be exactly 0 -- no cash and no debt existed to begin with, got {data['store_credit_amount']}"
    assert _credit_balance(cid, customer_id) == 0.0, \
        f"credit_balance must be exactly 0, got {_credit_balance(cid, customer_id)}"


# ── 3. Points sale + unrelated 500 debt: the unrelated debt must be untouched ─

def test_points_sale_return_never_touches_an_unrelated_debt(shop):
    """MEASURED (before this file's fix): the unrelated 500 debt drops to
    470 -- a real receivable written off by returning a DIFFERENT sale that
    owed nothing at all.

    Mutation that must turn this red again: same as test 1 -- drop the
    points_redeemed_amount subtraction from original_balance_due (which
    invents 30 of phantom balance_due_remaining for ar_forgiven to eat into
    the unrelated debt with).
    """
    cid, admin = shop
    pid = _create_product(admin, sell_price=100.0, tax_rate=0)
    other_pid = _create_product(admin, sell_price=500.0, tax_rate=0)
    customer_id = _create_customer(admin)
    _allow_credit(customer_id)
    _set_loyalty_point_value(admin, 1)
    _grant_ledger_points(cid, customer_id, 50)

    sale_resp = _sale(admin, [{'product_id': pid, 'quantity': 1}],
                       customer_id=customer_id, points_redeemed=30, amount_paid=70.0)
    assert sale_resp.status_code == 200, sale_resp.get_json()
    sale = sale_resp.get_json()['data']

    # The UNRELATED debt: a separate, fully-unpaid credit sale against the
    # SAME customer. Nothing about this sale involves points at all.
    debt_resp = _sale(admin, [{'product_id': other_pid, 'quantity': 1}],
                       customer_id=customer_id, payment_method='credit', amount_paid=0.0)
    assert debt_resp.status_code == 200, debt_resp.get_json()
    debt_sale = debt_resp.get_json()['data']
    assert debt_sale['balance_due'] == 500.0, debt_sale
    assert _credit_balance(cid, customer_id) == 500.0, \
        f"the unrelated credit sale must post exactly 500 of AR, got {_credit_balance(cid, customer_id)}"

    r = _return(admin, sale['id'], pid, quantity=1)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['ar_forgiven_amount'] == 0.0, \
        f"the points sale's return must never forgive any AR -- it never owed any, got {data['ar_forgiven_amount']}"
    assert data['store_credit_amount'] == 0.0, data
    assert _credit_balance(cid, customer_id) == 500.0, \
        f"the UNRELATED 500 debt must be untouched by this return, got {_credit_balance(cid, customer_id)}"


# ── 4. JOD 3dp tender: no fils created or destroyed on a full return ────────

def test_jod_full_return_refunds_the_exact_fils_tendered(shop):
    """MEASURED (before this file's fix): tender_refund_amount=4.01 against
    only 4.007 ever collected (3 fils CREATED -- the drawer goes short by
    it), and ar_forgiven_amount=6.115 against 6.118 actually owed, leaving
    the customer owing a residual 0.003 JOD after a FULL return.

    This isolates DEFECT 2 from DEFECT 1: no points are involved
    (points_redeemed_amount stays 0), so this failure is purely the
    `_record_payment` call in create_sale quantizing to 2dp instead of the
    company's real JOD (3dp) precision.

    Mutation that must turn this red again: drop the `currency=currency`
    argument from create_sale's own retained-cash `_record_payment(...)`
    call -- tender_refund_amount goes back to 4.01 and credit_balance back
    to a nonzero residue instead of exactly 0.
    """
    cid, admin = shop
    # Default install currency is JOD (tax_engine.DEFAULT_BASE_CURRENCY) --
    # no explicit settings write needed, matching
    # retail_sale_money_precision_test.py's own "(a) DEFAULT INSTALL" case.
    pid = _create_product(admin, sell_price=10.125, tax_rate=0)
    customer_id = _create_customer(admin)
    _allow_credit(customer_id)

    sale_resp = _sale(admin, [{'product_id': pid, 'quantity': 1}],
                       customer_id=customer_id, amount_paid=4.007)
    assert sale_resp.status_code == 200, sale_resp.get_json()
    sale = sale_resp.get_json()['data']
    assert sale['total'] == 10.125, sale
    assert sale['points_redeemed_amount'] == 0.0, sale
    assert sale['balance_due'] == 6.118, sale
    assert _credit_balance(cid, customer_id) == 6.118, \
        f"expected credit_balance=6.118 after the sale, got {_credit_balance(cid, customer_id)}"

    r = _return(admin, sale['id'], pid, quantity=1)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['refund_amount'] == 10.125, data
    assert data['tender_refund_amount'] == 4.007, \
        f"expected exactly 4.007 tendered back (not 4.01), got {data['tender_refund_amount']}"
    assert data['ar_forgiven_amount'] == 6.118, \
        f"expected exactly 6.118 forgiven (not 6.115), got {data['ar_forgiven_amount']}"
    assert data['store_credit_amount'] == 0.0, data
    assert _credit_balance(cid, customer_id) == 0.0, \
        f"credit_balance must be exactly 0 after a full return, no residue, got {_credit_balance(cid, customer_id)}"
