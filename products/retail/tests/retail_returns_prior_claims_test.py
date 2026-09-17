"""
Aura Retail -- return "prior claims" guard tests (VACUOUS-COVERAGE close-out,
retail-hardware-viewports crew, 2026-09-17).

create_return() (api/retail_api.py) carries TWO separate guards that both
subtract an EARLIER partial return's own claim from a pool before a LATER
partial return of the SAME sale is allowed to draw from it again:

  1. `tender_available = float(tender_collected) - float(_prior[0])`
     (~line 7862) -- already pinned, but by exactly ONE test
     (retail_returns_wave0_test.py::
     test_second_partial_return_only_draws_remaining_tender_pool). This file
     adds a SECOND, INDEPENDENT pin for it (different prices/amounts, same
     guard) -- see test_second_independent_pin_tender_guard_... below.

  2. `balance_due_remaining = max(0.0, original_balance_due - float(_prior[1]))`
     (~line 7898) -- PROVEN VACUOUS. Deleting the subtraction (leaving
     `balance_due_remaining = original_balance_due`) leaves
     retail_returns_wave0_test.py (17 tests), retail_payment_money_precision_
     test.py (15) and retail_sale_money_precision_test.py (21) ALL GREEN --
     53 tests, guard gone, nothing notices. This file closes that gap -- see
     test_second_partial_return_ar_forgiven_guard_... below.

Both guards protect the SAME kind of fact: a sale's own recorded consideration
(what it collected in tender, what it was ever charged as AR) is a POOL, and
successive partial returns of that one sale can only draw down what earlier
returns of the SAME sale left in the pool -- never draw the same dollar twice.

The two guards are NOT symmetric in what they protect, though -- measured, not
assumed, in response to a direct challenge on exactly this question (see
test_second_partial_return_ar_forgiven_guard_pins_the_classification_split_
not_net_money's own docstring for the full derivation, both algebraic and a
200,000-trial empirical sweep). Guard 1 (tender) protects real net money: the
till would physically pay out cash twice. Guard 2 (AR) does NOT -- whatever it
refuses `ar_forgiven`, `store_credit` silently absorbs at the same sign in the
SAME `customers.credit_balance` column (both write through the identical
`_adjust_credit(..., -amount, ...)` call), so the customer's TOTAL
credit_balance, every statement, and every aging bucket land on the same
number whether guard 2 is present or not. Guard 2 is a CLASSIFICATION guard:
it decides whether the uncollectible remainder is honestly labelled "this
sale's own debt, forgiven" versus "store credit issued against tender this
sale actually collected" -- a real, cashier-visible distinction (the
return-success toast shows different words and numbers for each), but not a
money guard in the sense guard 1 is.

Bootstrap pattern copied from retail_returns_wave0_test.py /
retail_cash_drawer_test.py (this suite's own established shape). Run ONE FILE
PER PROCESS (AUDIT-010):

    pytest products/retail/tests/retail_returns_prior_claims_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_priorclaims_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
# AURA_SITE_RELAY_ENABLED="0" -- this test seeds a WINDOWS-platform active
# license below (like retail_cash_drawer_test.py / retail_returns_wave0_test.py
# already do) and boots the real app; without this, a licensed WINDOWS
# instance elects itself a LAN relay hub and starts four background threads
# plus a TLS listener during test collection (AUDIT-010 sibling concern).
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
from database.schema import get_retail_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin(price_a=50.0, price_b=50.0, price_unrelated=500.0, stock=50):
    """New company + admin user + branch + THREE zero-tax products + a
    logged-in test client -- same shape as retail_returns_wave0_test.py's
    own _make_admin_and_product, widened to three lines: two same-value
    lines for the sale under test (so it can be returned in two matched
    partial slices) and one large line to manufacture an "unrelated" debt on
    the same customer."""
    email = f"prior-{uuid.uuid4().hex[:10]}@test.local"
    password = "PriorClaimsPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()

    rconn = get_retail_conn()
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)
    ).fetchone()[0]

    def _mk_product(sku, price):
        rconn.execute(
            "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,?,?,?,?,0)",
            (str(uuid.uuid4()), company_id, sku, sku, price / 2, price),
        )
        pid = rconn.execute(
            "SELECT id FROM products WHERE company_id=? AND sku=?", (company_id, sku)
        ).fetchone()[0]
        rconn.execute(
            "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
            (company_id, pid, branch_id, stock),
        )
        return pid

    pid_a = _mk_product(f'PC-A-{uuid.uuid4().hex[:6]}', price_a)
    pid_b = _mk_product(f'PC-B-{uuid.uuid4().hex[:6]}', price_b)
    pid_unrelated = _mk_product(f'PC-U-{uuid.uuid4().hex[:6]}', price_unrelated)
    rconn.commit()
    rconn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    client.get("/api/sub/retail/settings/tax")

    # sales.sale_number / returns.return_number carry bare (not company-
    # scoped) UNIQUE constraints, but _next_ref()'s counter starts at 1 per
    # company -- every test function's fresh company would otherwise
    # generate "SALE-000001"/"RET-000001" as its first doc and collide with
    # every other test function's first doc in this shared temp DB. Same
    # trick as retail_returns_wave0_test.py's own _make_admin_and_product.
    import random as _random
    dconn = get_retail_conn()
    for _doc_type in ('sale', 'return'):
        dconn.execute(
            "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
            (company_id, _doc_type, _random.randint(1, 5_000_000)),
        )
    dconn.commit()
    dconn.close()

    return client, company_id, branch_id, pid_a, pid_b, pid_unrelated


def _make_credit_customer(cid, name='Prior Claims Customer'):
    """credit_mode='unlimited' -- the company-wide default is 'none', which
    refuses ANY credit sale outright ('This customer is not allowed to buy
    on credit.', 400) -- identical helper to
    retail_returns_wave0_test.py's own _make_credit_customer."""
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


def _credit_balance(cid, customer_id):
    """No GET /customers/<id> route (only PATCH/DELETE) -- read the real
    column directly, same as retail_returns_wave0_test.py's identical
    helper."""
    rconn = get_retail_conn()
    bal = rconn.execute(
        "SELECT credit_balance FROM customers WHERE id=? AND company_id=?", (customer_id, cid)
    ).fetchone()[0]
    rconn.close()
    return bal


def _sell_one_line(client, customer_id, pid, amount_paid, payment_method):
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'customer_id': customer_id, 'amount_paid': amount_paid, 'payment_method': payment_method,
        'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _return_one_line(client, sale_id, pid, refund_method=None, reason='prior-claims test'):
    body = {
        'sale_id': sale_id, 'items': [{'product_id': pid, 'quantity': 1}],
        'reason': reason, 'idempotency_key': str(uuid.uuid4()),
    }
    if refund_method is not None:
        body['refund_method'] = refund_method
    r = client.post('/api/sub/retail/returns', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


# ── GAP 2: the `balance_due_remaining` AR-forgiveness guard (~retail_api.py:7898) ──

def test_second_partial_return_ar_forgiven_guard_pins_the_classification_split_not_net_money():
    """PROVEN VACUOUS gap this test closes: `balance_due_remaining = max(0.0,
    original_balance_due - float(_prior[1]))` is what stops a LATER partial
    return of a sale from re-claiming AR the sale already had forgiven by an
    EARLIER partial return of the SAME sale. Delete the `- float(_prior[1])`
    term and retail_returns_wave0_test.py (17), retail_payment_money_
    precision_test.py (15) and retail_sale_money_precision_test.py (21) all
    stay green -- 53 tests, guard gone, nothing notices -- so it needs its
    own pin regardless of what follows below.

    IS THIS A MONEY GUARD OR ONLY A CLASSIFICATION GUARD? Measured, in
    response to a direct challenge on exactly this question (2026-09-17),
    because the first draft of this test only showed that `credit_balance`
    lands on the SAME final number (460) whether the guard is present or
    not -- which raised the fair question of whether this guard protects
    any money at all.

    THE WRITE PATH (read, not assumed): `ar_forgiven_amount` and
    `store_credit_amount` both reach `customers.credit_balance` through the
    SAME call, same sign -- `_adjust_credit(conn, 'customers', ...,
    -amount, currency)` (retail_api.py ~8173-8176, both guarded by the same
    `> epsilon` check). They are genuinely interchangeable for that one
    column: whatever the guard refuses `ar_forgiven`, `store_credit` picks
    up instead (it is capped only by `settleable = min(refund,
    tender_available + balance_due_remaining)`, never by `current_balance`
    -- see core/retail/returns_settlement.py's DEFECT-1B docstring), at the
    same sign, in the same column.

    PROVEN THIS ALWAYS CANCELS FOR credit_balance, two ways, with NO
    mutation of retail_api.py (per the coordinator's constraint -- both
    checks below call the ALREADY-EXISTING, unmodified
    `returns_settlement.split_return_settlement` directly, or read the app
    live over HTTP):

      1. Algebraically: as long as a sale's cumulative returned value never
         exceeds its own total T (a SEPARATE guard elsewhere in
         create_return -- the "remain returnable" quantity check, not this
         one), the settleable ceiling for the next return telescopes to
         `T - tender_claimed_so_far - ar_claimed_so_far`, which is provably
         `>=` that return's own refund value REGARDLESS of whether
         `balance_due_remaining` is correctly decremented or left at the
         raw, un-decremented `original_balance_due` -- so `settleable`
         always equals `refund` exactly, in BOTH branches, and
         `tender_refund + ar_forgiven + store_credit` always sums to
         `refund` in both. `tender_refund` is governed by the OTHER guard
         (`_prior[0]`, untouched by this mutation) and is therefore
         identical between branches, which forces `ar_forgiven +
         store_credit` to be identical between branches too, always.
      2. Empirically: a 200,000-trial randomized sweep replayed 2-3
         sequential returns per sale against `split_return_settlement`
         directly, with random totals, random tender amounts, random
         cash/store_credit methods, AND -- the coordinator's own suggested
         probe -- a random external override of `current_balance` between
         returns (simulating "the customer paid part of it off some other
         way," exactly the case that module's own docstring names). Zero
         of 200,000 trials showed the guarded and buggy branches produce a
         different TOTAL `ar_forgiven + store_credit` for the same sale.
         The same harness found the SPLIT (ar_forgiven vs store_credit
         individually) differing in 38,837 of the 200,000 trials, so the
         zero above is a real finding, not a probe that can't tell the
         branches apart.

    CHECKED EVERY OTHER READER of these two columns, not just
    credit_balance: `core/retail/metrics.py`'s revenue definition reads
    only `refund_amount` (untouched by the split, by that module's own
    design). `customer_statement` (retail_api.py ~12780) reads NEITHER
    column -- it already has a documented, pre-existing gap where a
    return's AR adjustment never appears in that statement's event list at
    all. `aging_report` (~13640) buckets purely on the aggregate
    `customers.credit_balance` column and each party's oldest UNPAID SALE
    date -- blind to the split, and therefore invariant too, since
    credit_balance itself is proven invariant above. sync_service.py's
    apply side (~2149-2171) just replicates whichever numbers retail_api.py
    put on the wire onto the receiving device's OWN row -- it doesn't
    re-derive or re-judge them.

    THE ONE PLACE THE SPLIT IS VISIBLY DIFFERENT: subsystem-retail.js's
    return-success toast (~9821-9829) reads `ar_forgiven_amount` and
    `store_credit_amount` separately and shows the cashier different WORDS
    and different NUMBERS depending on the split -- "+10 credit forgiven"
    here, where the buggy branch would show "+50 credit forgiven" and no
    store-credit line at all. That is real, and it is why this guard is
    worth pinning: a cashier reading that toast is told a materially
    different story about what this sale still holds. But it is a
    transient UI message, not a persisted figure -- the customer's actual
    account balance, every statement, and every aging bucket come out
    identical either way.

    CONCLUSION: this guard is a classification-only guard for money that
    stays inside `customers.credit_balance` -- it decides whether a
    return's uncollectible remainder is honestly labelled "this sale's own
    debt, forgiven" versus "credit issued against tender this sale actually
    took," never how much total credit the customer ends up with. That
    label is real and worth pinning (it is the honest answer to "why did we
    only forgive 10 and not 50"), but it is not a net-money consequence in
    any reachable case tried here. This test pins the label
    (`ar_forgiven_amount`/`store_credit_amount` diverge: 10/40 guarded vs
    50/0 buggy), NOT a money total -- `credit_balance` is asserted below as
    the CONTROL that proves this point (460 either way), not as the thing
    the guard protects.

    Scenario, unchanged from the original draft: a customer already carries
    500 of UNRELATED debt (a separate, fully-unpaid sale) -- kept because it
    is still what makes `current_balance` ample enough that `ar_forgiven`
    is never itself capped by it, isolating the guard's own effect. A
    SECOND sale to the same customer -- two 50-value lines, 40 tendered in
    cash, 60 left on credit -- "a sale that owed 60" -- is returned in two
    50-value slices, both routed to store credit.
    """
    client, cid, bid, pid_a, pid_b, pid_u = _make_admin(price_a=50.0, price_b=50.0, price_unrelated=500.0)
    customer_id = _make_credit_customer(cid)

    # Unrelated debt: a separate, fully-unpaid sale of the 500 line to the
    # SAME customer -- credit_balance now carries 500 that has nothing to do
    # with the sale under test below.
    unrelated_sale = _sell_one_line(client, customer_id, pid_u, amount_paid=0.0, payment_method='credit')
    assert unrelated_sale['balance_due'] == 500.0
    assert _credit_balance(cid, customer_id) == 500.0

    # The sale under test: two 50-value lines (total 100), 40 tendered in
    # cash, 60 left on credit -- "a sale that owed 60".
    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid_a, 'quantity': 1}, {'product_id': pid_b, 'quantity': 1}],
        'customer_id': customer_id, 'amount_paid': 40.0, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    })
    assert sale.status_code == 200, sale.get_json()
    sale = sale.get_json()['data']
    assert sale['total'] == 100.0
    assert sale['balance_due'] == 60.0
    assert _credit_balance(cid, customer_id) == 560.0  # 500 unrelated + 60 this sale

    # First partial return: line A, value 50, routed to store credit.
    d1 = _return_one_line(client, sale['id'], pid_a, refund_method='store_credit')
    assert d1['refund_amount'] == 50.0
    assert d1['tender_refund_amount'] == 0.0
    assert d1['ar_forgiven_amount'] == 50.0
    assert d1['store_credit_amount'] == 0.0
    assert _credit_balance(cid, customer_id) == 510.0

    # Second partial return: line B, ALSO value 50, ALSO store credit. The
    # sale only ever owed 60; 50 of that was already forgiven by the first
    # return, so at most 10 may legitimately be forgiven as AR here.
    d2 = _return_one_line(client, sale['id'], pid_b, refund_method='store_credit')
    assert d2['refund_amount'] == 50.0
    assert d2['tender_refund_amount'] == 0.0
    assert d2['ar_forgiven_amount'] == 10.0, (
        "THE VACUOUS-COVERAGE gap: balance_due_remaining must be capped by "
        "this sale's own 60 minus the 50 the FIRST return already forgave "
        f"(=10), not by the raw, un-decremented 60 again. got ar_forgiven_amount={d2['ar_forgiven_amount']}"
    )
    assert d2['store_credit_amount'] == 40.0, \
        f"the 40 the guard refuses as AR must still land as legitimate store credit, got {d2['store_credit_amount']}"

    total_ar_forgiven = d1['ar_forgiven_amount'] + d2['ar_forgiven_amount']
    assert total_ar_forgiven == 60.0, (
        "the AR bucket specifically must equal exactly what this sale ever "
        f"owed (60), not re-claim it a second time in that SAME bucket -- got {total_ar_forgiven}"
    )
    # THE CONTROL, not the guard's actual protection (see this test's own
    # docstring, "IS THIS A MONEY GUARD..."): credit_balance lands on 460
    # EITHER WAY -- proven, not assumed, both algebraically and by a
    # 200,000-trial sweep of split_return_settlement -- because store_credit
    # (asserted above) silently absorbs whatever the guard refuses
    # ar_forgiven, same sign, same column. Asserted here specifically so a
    # future change that made this figure diverge would be caught, not to
    # claim the GUARD produces this number.
    assert _credit_balance(cid, customer_id) == 460.0


# ── SECOND, INDEPENDENT PIN for the tender guard (~retail_api.py:7862) ──
# `tender_available = float(tender_collected) - float(_prior[0])` is already
# pinned by retail_returns_wave0_test.py::
# test_second_partial_return_only_draws_remaining_tender_pool -- but by
# exactly ONE test. This is a second, independent pin: different prices,
# different amounts, same guard, no unrelated-debt fixture needed.

def test_second_independent_pin_tender_guard_prevents_double_drawing_the_tender_pool():
    """Sale: two 80-value lines (total 160), 30 tendered in cash, 130 left
    on credit. First return (line A, default 'cash' refund_method) drains
    the ENTIRE 30 tender pool and forgives the remaining 50 as AR. The
    second return (line B, also value 80) must draw ZERO further tender --
    the pool is already empty -- not another 30.

    Mutation that must turn this red: drop the `- float(_prior[0])` term
    from `tender_available` (leave `balance_due_remaining`'s OWN guard, the
    one the sibling test above pins, untouched) -- the second return would
    wrongly draw ANOTHER 30 of tender (60 total across both returns,
    overpaying the customer by 30 in cash the till never actually holds a
    second time), and credit_balance would land on 30 instead of 0.
    """
    client, cid, bid, pid_a, pid_b, pid_u = _make_admin(price_a=80.0, price_b=80.0, price_unrelated=500.0)
    customer_id = _make_credit_customer(cid)

    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid_a, 'quantity': 1}, {'product_id': pid_b, 'quantity': 1}],
        'customer_id': customer_id, 'amount_paid': 30.0, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    })
    assert sale.status_code == 200, sale.get_json()
    sale = sale.get_json()['data']
    assert sale['total'] == 160.0
    assert sale['balance_due'] == 130.0
    assert _credit_balance(cid, customer_id) == 130.0

    # First return: line A (value 80), default 'cash' method -- drains the
    # ENTIRE 30 tender pool (capped there) and forgives the rest (50) as AR.
    d1 = _return_one_line(client, sale['id'], pid_a)
    assert d1['refund_amount'] == 80.0
    assert d1['tender_refund_amount'] == 30.0
    assert d1['ar_forgiven_amount'] == 50.0
    assert _credit_balance(cid, customer_id) == 80.0

    # Second return: line B, ALSO value 80. The tender pool is already
    # drained to 0 by the first return -- this one must draw ZERO tender.
    d2 = _return_one_line(client, sale['id'], pid_b)
    assert d2['refund_amount'] == 80.0
    assert d2['tender_refund_amount'] == 0.0, (
        "the tender pool (30) was already fully drawn by the FIRST return -- "
        "a second nonzero draw here means tender_available failed to "
        f"subtract the prior return's own tender_refund_amount. got {d2['tender_refund_amount']}"
    )
    assert d2['ar_forgiven_amount'] == 80.0
    assert _credit_balance(cid, customer_id) == 0.0, \
        f"must land on exactly 0 (130 owed, fully settled by 50+80 AR plus the 30 tender) -- got {_credit_balance(cid, customer_id)}"
