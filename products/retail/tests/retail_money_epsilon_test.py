"""Aura Retail -- the "is this amount really zero" gates use the SHOP's own
minor unit, not a hardcoded half-cent.

`products/retail/backend/api/retail_api.py` decided that question with a
literal `0.005` in roughly twenty-five places: `if amount_paid > 0.005`, `if
balance_due > 0.005`, `payment_status = 'paid' if amount_paid >= total -
0.005`, `WHERE COALESCE(credit_balance,0) > 0.005`, and so on.

`0.005` is HALF A CENT. It is exactly right on a 2-decimal currency and wrong
on JOD, whose minor unit is a fil at 0.001 -- so every one of those gates
treated anything up to FIVE FILS as zero.

WHY SUCH SMALL AMOUNTS ARE WORTH A TEST FILE. These are GATES, not roundings.
Each flips a boolean about whether money EXISTS. A sub-threshold debt is not
recorded a fil short, it is not recorded AT ALL, and every later read then
reasons from "there was never a debt" -- no `payments` row, no
`credit_balance` entry, nothing in the debtor book, nothing to settle a
return against. That is how a rounding-sized number becomes a logic error.

THE PAIRED SHAPE EVERY TEST BELOW USES, because it is the whole argument in
one scenario: an identical sale -- list price 10, tender 9.996 -- must behave
OPPOSITELY in the two currencies, and both answers are correct.

    JOD (3dp, fils)     9.996 leaves 0.004 owed   -> a real 4-fil debt
    USD (2dp, cents)    9.996 IS 10.00            -> nothing owed

Before the sweep both currencies gave the USD answer, so a Jordanian shop's
four fils vanished. A fix that instead gave both the JOD answer would be just
as wrong in the other direction, which is why every test here has its USD
control right beside it rather than in some other file.

`_money_epsilon` is the single helper all those gates now read, so they
cannot drift apart again -- the specific failure ffa221a5 hit when one site
was made finer alone, handing a customer store credit for a debt the shop had
never booked.

Self-contained bootstrap, per this directory's convention (no conftest.py).

Run:
    pytest products/retail/tests/retail_money_epsilon_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_epsilon_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
# See retail_returns_prior_claims_test.py for why a licensed WINDOWS test
# instance must be told not to elect itself the LAN relay hub.
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
from api.retail_api import _money_epsilon  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── harness ───────────────────────────────────────────────────────────────────

def _make_admin(currency=None, sell_price=10.0, stock=50):
    """New company + admin + branch + one zero-tax product + logged-in client.

    `currency=None` leaves the shop with NO explicit base_currency row, which
    is the common fresh-install state and resolves to JOD through
    `_company_currency`'s documented default -- the exact path that made this
    bug the default rather than an edge case."""
    email = f"eps-{uuid.uuid4().hex[:10]}@test.local"
    password = "EpsilonPW1"
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
    if currency:
        rconn.execute(
            "INSERT INTO retail_settings (company_id, skey, svalue) VALUES (?,?,?) "
            "ON CONFLICT(company_id,skey) DO UPDATE SET svalue=excluded.svalue",
            (company_id, 'base_currency', currency))
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)
    ).fetchone()[0]
    sku = f'EPS-{uuid.uuid4().hex[:8]}'
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, sku, sku, 1.0, sell_price),
    )
    pid = rconn.execute(
        "SELECT id FROM products WHERE company_id=? AND sku=?", (company_id, sku)
    ).fetchone()[0]
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (company_id, pid, branch_id, stock),
    )
    rconn.commit()
    rconn.close()

    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    client.get(f"{API}/settings/tax")

    import random as _random
    dconn = get_retail_conn()
    for _doc_type in ('sale', 'return', 'po'):
        dconn.execute(
            "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
            (company_id, _doc_type, _random.randint(1, 5_000_000)),
        )
    dconn.commit()
    dconn.close()

    return client, company_id, branch_id, pid


def _make_credit_customer(cid, name='Epsilon Customer'):
    rconn = get_retail_conn()
    customer_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO customers (id,company_id,name,credit_mode,credit_limit,credit_balance) "
        "VALUES (?,?,?,'unlimited',0,0)",
        (customer_id, cid, name),
    )
    rconn.commit()
    rconn.close()
    return customer_id


def _make_supplier(cid, name='Epsilon Supplier'):
    rconn = get_retail_conn()
    supplier_id = str(uuid.uuid4())
    rconn.execute("INSERT INTO suppliers (id,company_id,name,credit_balance) VALUES (?,?,?,0)",
                  (supplier_id, cid, name))
    rconn.commit()
    rconn.close()
    return supplier_id


def _sell(client, customer_id, pid, amount_paid, payment_method='credit'):
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'customer_id': customer_id, 'amount_paid': amount_paid,
        'payment_method': payment_method, 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _balance(table, cid, party_id):
    rconn = get_retail_conn()
    bal = rconn.execute(
        f"SELECT credit_balance FROM {table} WHERE id=? AND company_id=?", (party_id, cid)
    ).fetchone()[0]
    rconn.close()
    return bal


# ── 1. the helper itself ──────────────────────────────────────────────────────

def test_the_epsilon_is_half_the_currencys_own_minor_unit():
    """A unit check on the one value every gate in the sweep reads. Half a
    minor unit is the correct slack for "is this really zero" once the
    amounts themselves are already quantized to that unit: it absorbs float
    representation noise and nothing else, so the smallest REAL amount --
    one minor unit -- always reads as non-zero."""
    assert _money_epsilon('JOD') == 0.0005, "JOD has 1000 fils; half a fil is 0.0005"
    assert _money_epsilon('KWD') == 0.0005, "the other 3-decimal currencies move with it"
    assert _money_epsilon('USD') == 0.005, "a 2-decimal currency keeps the historical half-cent"

    # `None` reproduces the pre-sweep constant byte for byte. This is the
    # same contract `_money` carries, and for the same reason: an unaudited
    # caller keeps exactly the behaviour it had.
    assert _money_epsilon(None) == 0.005
    # An unknown code must not raise inside a money gate. `currency_quantum`
    # falls back to 2 decimals for a code it does not know, so this degrades
    # to the historical value rather than to zero -- an epsilon of 0 would
    # make every float comparison in this file sensitive to representation
    # noise.
    assert _money_epsilon('ZZZ') == 0.005


# ── 2. a real debt, four fils wide ────────────────────────────────────────────

def test_a_four_fil_shortfall_is_recorded_as_a_real_debt_on_a_three_decimal_currency():
    """List price 10.000, customer tenders 9.996, on credit. They owe four
    fils. Before the sweep `balance_due` (0.004) was not > 0.005, so
    create_sale recorded NO debt at all -- no credit_balance entry -- and the
    shop's books said this customer owed nothing."""
    client, cid, _bid, pid = _make_admin(currency=None, sell_price=10.0)
    customer_id = _make_credit_customer(cid)

    sale = _sell(client, customer_id, pid, amount_paid=9.996)
    assert sale['total'] == 10.0 and sale['amount_paid'] == 9.996, sale

    assert _balance('customers', cid, customer_id) == 0.004, (
        "four fils really are owed and must be on the books; got "
        f"{_balance('customers', cid, customer_id)!r}"
    )


def test_the_identical_shape_on_a_two_decimal_currency_is_still_correctly_nothing():
    """THE ALLOW-HALF. The same sale in USD: 9.996 tendered against a 10.00
    list price IS ten dollars -- there is no coin smaller than a cent for the
    remainder to be owed in. The sweep must not make a 2-decimal shop start
    carrying sub-cent receivables, which is precisely what a fix that swapped
    one hardcoded constant for a smaller hardcoded constant would have done.

    This is the control that proves the epsilon is derived from the CURRENCY
    rather than merely made smaller."""
    client, cid, _bid, pid = _make_admin(currency='USD', sell_price=10.0)
    customer_id = _make_credit_customer(cid)

    sale = _sell(client, customer_id, pid, amount_paid=9.996)
    assert sale['amount_paid'] == 10.0, (
        f"precondition: USD quantizes the tender to cents, got {sale['amount_paid']!r}")

    assert _balance('customers', cid, customer_id) == 0.0, (
        "nothing is owed on a 2-decimal currency; got "
        f"{_balance('customers', cid, customer_id)!r}"
    )


# ── 3. the debt is visible everywhere it should be ────────────────────────────

def test_a_four_fil_debtor_appears_in_receivables_and_in_its_total():
    """Recording the debt is only half the job -- the debtor book filters on
    the same epsilon, so a gate left at 0.005 there would hide a customer the
    sale had correctly booked, and `total_receivable` would disagree with the
    ledger. Both the rows and the total are checked, because they are two
    separate queries that must use the same threshold (see
    retail_ar_ap_totals_test.py for the bug that taught this file's
    neighbours the same lesson)."""
    client, cid, _bid, pid = _make_admin(currency=None, sell_price=10.0)
    customer_id = _make_credit_customer(cid)
    _sell(client, customer_id, pid, amount_paid=9.996)

    r = client.get(f'{API}/customers/receivables')
    assert r.status_code == 200, r.get_json()
    body = r.get_json()

    assert customer_id in [row['id'] for row in body['data']], (
        f"a four-fil debtor must be listed in the debtor book: {body['data']}")
    assert body['total_receivable'] == 0.004, body
    assert body['total_receivable'] == sum(row['credit_balance'] for row in body['data'])


def test_a_four_fil_debtor_is_aged_rather_than_dropped_from_the_report():
    """The aging report runs TWO queries -- "who owes anything" and "what is
    their oldest unpaid document" -- and they must share one epsilon. Split
    them and a party can be selected by the first while the second finds no
    unpaid document at all, which silently ages a real debt at zero days into
    the 'current' bucket."""
    client, cid, _bid, pid = _make_admin(currency=None, sell_price=10.0)
    customer_id = _make_credit_customer(cid)
    _sell(client, customer_id, pid, amount_paid=9.996)

    r = client.get(f'{API}/reports/aging?type=receivable')
    assert r.status_code == 200, r.get_json()
    # `data` IS the bucket map -- {'current': .., '1_30': .., ...} -- not a
    # wrapper around one.
    buckets = r.get_json()['data']
    assert round(sum(buckets.values()), 6) == 0.004, (
        f"the four fils must be aged into some bucket, got {buckets}")


# ── 4. the supplier / purchase-order half ─────────────────────────────────────

def test_a_four_fil_down_payment_makes_a_purchase_order_partial_not_unpaid():
    """create_purchase_order decides `payment_status`, whether to write a
    `payments` row, and whether to push the balance onto AP -- all three off
    the same epsilon. A four-fil down-payment on JOD used to be none of those
    things: status 'unpaid', no payment row, and the shop's own money simply
    not recorded as having left."""
    client, cid, _bid, pid = _make_admin(currency=None, sell_price=10.0)
    supplier_id = _make_supplier(cid)

    r = client.post(f'{API}/purchase-orders', json={
        'supplier_id': supplier_id,
        'items': [{'product_id': pid, 'quantity': 1, 'unit_cost': 10.0}],
        'amount_paid': 0.004,
    })
    assert r.status_code == 200, r.get_json()

    rconn = get_retail_conn()
    po = rconn.execute(
        "SELECT payment_status, amount_paid FROM purchase_orders WHERE company_id=? AND supplier_id=?",
        (cid, supplier_id)).fetchone()
    paid_rows = rconn.execute(
        "SELECT COUNT(*) FROM payments WHERE company_id=? AND party_type='supplier' AND party_id=?",
        (cid, supplier_id)).fetchone()[0]
    rconn.close()

    assert po['payment_status'] == 'partial', f"got {po['payment_status']!r}"
    assert paid_rows == 1, "the four fils that left the till must be in the ledger"
    assert _balance('suppliers', cid, supplier_id) == 9.996


def test_a_purchase_order_four_fils_short_is_not_called_fully_paid():
    """pay_purchase_order's own `'paid' if new_paid >= total - epsilon` gate,
    which must agree with create_purchase_order's. Paying 9.996 of a 10.000
    PO leaves four fils genuinely outstanding, and calling that 'paid' is how
    a supplier balance quietly stops matching what the shop actually sent."""
    client, cid, _bid, pid = _make_admin(currency=None, sell_price=10.0)
    supplier_id = _make_supplier(cid)

    r = client.post(f'{API}/purchase-orders', json={
        'supplier_id': supplier_id,
        'items': [{'product_id': pid, 'quantity': 1, 'unit_cost': 10.0}],
        'amount_paid': 0.0,
    })
    assert r.status_code == 200, r.get_json()
    po_id = r.get_json()['data']['id']

    p = client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 9.996})
    assert p.status_code == 200, p.get_json()
    assert p.get_json()['data']['payment_status'] == 'partial', p.get_json()
    assert _balance('suppliers', cid, supplier_id) == 0.004


def test_the_same_purchase_order_shape_on_a_two_decimal_currency_is_fully_paid():
    """The supplier-side control, mirroring the sale-side one above: on USD,
    9.996 against a 10.00 PO IS the whole amount, and the PO must read
    'paid'. The two currencies must disagree here, and both be right."""
    client, cid, _bid, pid = _make_admin(currency='USD', sell_price=10.0)
    supplier_id = _make_supplier(cid)

    r = client.post(f'{API}/purchase-orders', json={
        'supplier_id': supplier_id,
        'items': [{'product_id': pid, 'quantity': 1, 'unit_cost': 10.0}],
        'amount_paid': 0.0,
    })
    assert r.status_code == 200, r.get_json()
    po_id = r.get_json()['data']['id']

    p = client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 9.996})
    assert p.status_code == 200, p.get_json()
    assert p.get_json()['data']['payment_status'] == 'paid', p.get_json()
    assert _balance('suppliers', cid, supplier_id) == 0.0


# ── 5. the gates agree with each other ────────────────────────────────────────

def test_a_return_settles_the_four_fil_debt_the_sale_created():
    """THE AGREEMENT PROPERTY, which is the reason every site moved at once
    rather than the obvious ones first. create_return has to decide "did this
    sale become a debt?" exactly the way create_sale decided it. Move one
    gate and not the other and the return either forgives a debt that was
    never booked (inventing store credit) or refuses to forgive one that was.

    Same four-fil sale as above, returned in full: the tender comes back as
    tender, the four fils come back as AR forgiveness, and the customer ends
    at zero with nothing left in the third bucket."""
    client, cid, _bid, pid = _make_admin(currency=None, sell_price=10.0)
    customer_id = _make_credit_customer(cid)
    sale = _sell(client, customer_id, pid, amount_paid=9.996)
    assert _balance('customers', cid, customer_id) == 0.004

    r = client.post(f'{API}/returns', json={
        'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': 1}],
        'reason': 'epsilon agreement test', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()

    rconn = get_retail_conn()
    row = rconn.execute(
        "SELECT tender_refund_amount, ar_forgiven_amount, store_credit_amount "
        "FROM returns WHERE sale_id=? AND company_id=?", (sale['id'], cid)).fetchone()
    rconn.close()

    assert row['ar_forgiven_amount'] == 0.004, (
        "the four fils create_sale booked are exactly what the return forgives; got "
        f"ar_forgiven={row['ar_forgiven_amount']!r} store_credit={row['store_credit_amount']!r}")
    assert row['store_credit_amount'] == 0.0, (
        "nothing may spill into store credit -- a non-zero here means the two "
        "functions disagreed about how much of this sale was ever a debt")
    assert row['tender_refund_amount'] == 9.996
    assert _balance('customers', cid, customer_id) == 0.0


def test_the_four_fil_debt_reconciles_on_the_customer_statement():
    """And it must be VISIBLE, not merely stored. customer_statement mirrors
    create_sale's gate to decide whether a sale is a charge at all, so an
    epsilon left behind there would show a customer a balance of 0.004 with
    no event that explains it."""
    client, cid, _bid, pid = _make_admin(currency=None, sell_price=10.0)
    customer_id = _make_credit_customer(cid)
    _sell(client, customer_id, pid, amount_paid=9.996)

    r = client.get(f'{API}/customers/{customer_id}/statement')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']

    assert data['balance'] == 0.004
    assert data['events'], "the charge that created the four-fil debt must be an event"
    assert data['events'][-1]['running_balance'] == 0.004, data['events']
