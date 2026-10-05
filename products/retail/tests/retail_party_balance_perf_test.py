"""Aura Retail v44 (T5) -- what deriving party balances costs on a busy shop.

v44 stopped trusting `customers.credit_balance` as the figure and derives it
from shop-wide rows (core/retail/party_balance.py). That moves work onto paths
that used to read one column, so this file measures the four of them on a shop
the size of the verifier's dataset: 500 debtors, 20,000 sales (15,000 on
credit, 5,000 walk-in cash), 4,000 account payments and 3,000 returns.

  PATH                                        BUDGET (plan T5)   CEILING ASSERTED (2x, CI-safe)
  receivables route (all debtors, batch)      <= 0.5 s           <= 1.0 s
  refresh_all_companies (EVERY pull runs it)  <= 100 ms          <= 200 ms
  one party's derivation                      <=   5 ms          <=  10 ms
  create_sale route, credit sale              <= +10 % vs v43    absolute ceiling below

The ceilings are deliberately loose -- a slow CI box must not flake them. The
numbers are PRINTED (run with -s) and are the evidence; the assertions only
catch an order-of-magnitude regression such as a per-party loop or a missing
index. The create_sale budget is a RATIO against the pre-v44 tree, which a test
cannot hold (there is no pre-v44 tree to run in CI): it was measured by running
this same file in a detached worktree at 5ee7357a (the commit before the
writers were switched) and at HEAD; the figures are in the commit message. Here
the route only has an absolute ceiling.

Run (one file per process; see run_all_tests.py):
    python -m pytest products/retail/tests/retail_party_balance_perf_test.py -v -s
"""
import os
import shutil
import statistics
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_party_perf_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
# AURA_SITE_RELAY_ENABLED="0": an active WINDOWS licence elects a till the LAN
# hub, which would start background threads inside a timing test.
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
from core.retail import party_balance  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'
N_DEBTORS = 500
CREDIT_SALES_PER_DEBTOR = 30           # 15,000 credit sales
N_CASH_SALES = 5000                    # + 5,000 walk-in = 20,000 sales
PAYMENTS_PER_DEBTOR = 8                # 4,000 account payments
RETURNS_PER_DEBTOR = 6                 # 3,000 returns (forgiving 1.0 each)
SALE_SAMPLES = 30

RECEIVABLES_CEILING_S = 1.0
REFRESH_ALL_CEILING_MS = 200
SINGLE_PARTY_CEILING_MS = 10
#: measured ~10x below this on the dev box; only an order-of-magnitude guard.
CREATE_SALE_CEILING_MS = 1000


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _best(fn, runs=3):
    out = []
    for _ in range(runs):
        started = time.perf_counter()
        fn()
        out.append((time.perf_counter() - started) * 1000)
    return out


@pytest.fixture(scope="module")
def shop():
    """One company with the big fixture, logged in. Built once: the raw INSERTs
    are the shape of the data (what a busy shop's tables look like), not the
    routes that would have written it."""
    email = f"perf-{uuid.uuid4().hex[:10]}@test.local"
    password = "PartyPerfPW1"
    cid = str(uuid.uuid4())
    reg = registry_conn()
    reg.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), cid, "EMP-0001", email, hash_password(password), "admin", "active"))
    reg.commit(); reg.close()

    conn = get_retail_conn()
    conn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (cid,))
    bid = conn.execute("SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (cid,)).fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                 (cid, 'business_timezone', 'Asia/Amman'))
    conn.commit(); conn.close()

    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    client.get(f"{API}/settings/tax")
    r = client.post(f"{API}/settings/credit", json={"base_currency": "JOD"})
    assert r.status_code == 200, r.get_json()

    import random
    conn = get_retail_conn()
    for doc_type in ('sale', 'return'):
        conn.execute("INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
                     (cid, doc_type, random.randint(1, 5_000_000)))

    now = datetime(2026, 6, 15, 7, 0, 0, tzinfo=timezone.utc)

    def stamp(days):
        t = now - timedelta(days=days)
        return (t + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S'), t.strftime('%Y-%m-%d %H:%M:%S')

    debtors, sales, pays, rets = [], [], [], []
    sale_numbers = 0
    for i in range(N_DEBTORS):
        c = str(uuid.uuid4())
        debtors.append((c, cid, f'Perf {i}', 'unlimited', 0, 0))
    conn.executemany("INSERT INTO customers (id,company_id,name,credit_mode,credit_limit,credit_balance) "
                     "VALUES (?,?,?,?,?,?)", debtors)

    for (c, *_rest) in debtors:
        for j in range(CREDIT_SALES_PER_DEBTOR):
            sale_numbers += 1
            ca, cu = stamp(5 + j * 3)
            sales.append((cid, f'PS-{sale_numbers}-{uuid.uuid4().hex[:6]}', bid, c, 10.0, 10.0, 0, 'credit',
                          'completed', ca, cu))
        for j in range(PAYMENTS_PER_DEBTOR):
            ca, _cu = stamp(10 + j * 10)
            pays.append((cid, f'PP-{uuid.uuid4().hex[:10]}', 'customer', c, 'in', 5.0, 'JOD', 1, 'cash',
                         'active', None, ca))
    for k in range(N_CASH_SALES):
        sale_numbers += 1
        ca, cu = stamp(k % 300)
        sales.append((cid, f'PS-{sale_numbers}-{uuid.uuid4().hex[:6]}', bid, None, 3.0, 3.0, 3.0, 'cash',
                      'completed', ca, cu))
    conn.executemany(
        "INSERT INTO sales (company_id, sale_number, branch_id, customer_id, subtotal, total, amount_paid, "
        "payment_method, status, created_at, created_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?)", sales)
    conn.executemany(
        "INSERT INTO payments (company_id, reference, party_type, party_id, direction, amount, currency, fx_rate, "
        "method, status, related_type, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", pays)

    # Returns: RETURNS_PER_DEBTOR forgiving 1.0 each, against that debtor's sales.
    sale_ids = {}
    for sid, cust in conn.execute(
            "SELECT id, customer_id FROM sales WHERE company_id=? AND customer_id IS NOT NULL", (cid,)).fetchall():
        sale_ids.setdefault(cust, []).append(sid)
    for (c, *_rest) in debtors:
        for j, sid in enumerate(sale_ids[c][:RETURNS_PER_DEBTOR]):
            ca, cu = stamp(2 + j)
            rets.append((cid, f'PR-{uuid.uuid4().hex[:10]}', sid, bid, 1.0, 'completed', ca, cu, 1.0, 0.0, 0))
    conn.executemany(
        "INSERT INTO returns (company_id, return_number, sale_id, branch_id, refund_amount, status, created_at, "
        "created_at_utc, ar_forgiven_amount, store_credit_amount, tender_refund_amount) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rets)
    conn.commit()

    # Steady state: the cache already agrees with the derivation (what the boot
    # and every post-pull refresh leave behind).
    party_balance.refresh_cache(conn, cid, 'customers', currency='JOD')
    conn.commit()
    n_sales = conn.execute("SELECT COUNT(*) FROM sales WHERE company_id=?", (cid,)).fetchone()[0]
    n_returns = conn.execute("SELECT COUNT(*) FROM returns WHERE company_id=?", (cid,)).fetchone()[0]
    n_pays = conn.execute("SELECT COUNT(*) FROM payments WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()
    print(f"\nPERF_FIXTURE {N_DEBTORS} debtors / {n_sales} sales / {n_pays} payments / {n_returns} returns")
    assert n_sales == 20000 and n_returns == 3000 and n_pays == 4000
    return client, cid, bid, [d[0] for d in debtors]


def test_receivables_route_over_500_debtors(shop):
    client, cid, _bid, _ids = shop
    client.get(f"{API}/customers/receivables")                 # warm: schema check, page cache
    timings = _best(lambda: client.get(f"{API}/customers/receivables"))
    body = client.get(f"{API}/customers/receivables").get_json()
    print(f"PERF receivables route: {[round(t) for t in timings]} ms (best {round(min(timings))})  "
          f"budget 500 ms, ceiling {RECEIVABLES_CEILING_S * 1000:.0f} ms")
    assert len(body['data']) == N_DEBTORS
    # 30 charges of 10, 8 payments of 5, 6 forgiven returns of 1 -> 300 - 40 - 6
    assert body['total_receivable'] == pytest.approx(N_DEBTORS * 254.0, abs=0.001)
    assert min(timings) / 1000 <= RECEIVABLES_CEILING_S, timings


def test_refresh_all_companies_the_post_pull_hook_cost(shop):
    _client, _cid, _bid, ids = shop
    conn = get_retail_conn()
    try:
        party_balance.refresh_all_companies(conn)                # warm
        steady = _best(lambda: party_balance.refresh_all_companies(conn))
        changed = party_balance.refresh_all_companies(conn)
        assert changed == 0, "steady state must rewrite nothing"
        # Worst case: every cached figure is stale (an upgrade, or a pull that
        # touched every debtor) -- informational, the UPDATE count is the cost.
        conn.execute("UPDATE customers SET credit_balance = credit_balance + 1")
        started = time.perf_counter()
        changed = party_balance.refresh_all_companies(conn)
        stale_ms = (time.perf_counter() - started) * 1000
        conn.commit()
        assert changed == N_DEBTORS
    finally:
        conn.close()
    print(f"PERF refresh_all_companies steady state: {[round(t, 1) for t in steady]} ms (best {min(steady):.1f})  "
          f"budget 100 ms, ceiling {REFRESH_ALL_CEILING_MS} ms; all-{N_DEBTORS}-stale rewrite: {stale_ms:.1f} ms")
    assert min(steady) <= REFRESH_ALL_CEILING_MS, steady


def test_one_partys_derivation(shop):
    _client, cid, _bid, ids = shop
    conn = get_retail_conn()
    try:
        party_balance.customer_balance(conn, cid, ids[0], 'JOD')           # warm
        samples = []
        for pid in ids[:100]:
            started = time.perf_counter()
            bal = party_balance.customer_balance(conn, cid, pid, 'JOD')
            samples.append((time.perf_counter() - started) * 1000)
            assert float(bal) == pytest.approx(254.0, abs=0.001)
    finally:
        conn.close()
    print(f"PERF single-party derivation over 100 debtors: median {statistics.median(samples):.2f} ms, "
          f"max {max(samples):.2f} ms  budget 5 ms, ceiling {SINGLE_PARTY_CEILING_MS} ms")
    assert statistics.median(samples) <= SINGLE_PARTY_CEILING_MS, samples


def test_create_sale_route_on_a_busy_shop(shop):
    client, cid, bid, ids = shop
    conn = get_retail_conn()
    sku = f'PERF-{uuid.uuid4().hex[:8]}'
    conn.execute("INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,?,?,?,?,0)",
                 (str(uuid.uuid4()), cid, sku, sku, 5.0, 10.0))
    pid = conn.execute("SELECT id FROM products WHERE company_id=? AND sku=?", (cid, sku)).fetchone()[0]
    conn.execute("INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
                 (cid, pid, bid, 100000))
    conn.commit(); conn.close()

    def ring(customer_id):
        started = time.perf_counter()
        r = client.post(f"{API}/sales", json={
            'items': [{'product_id': pid, 'quantity': 1}], 'customer_id': customer_id,
            'amount_paid': 0, 'payment_method': 'credit', 'idempotency_key': str(uuid.uuid4())})
        elapsed = (time.perf_counter() - started) * 1000
        assert r.status_code == 200, r.get_json()
        return elapsed

    ring(ids[0])                                                # warm
    samples = [ring(ids[i + 1]) for i in range(SALE_SAMPLES)]
    median = statistics.median(samples)
    print(f"PERF create_sale (credit, debtor with ~44 rows, 20k-sale shop): median {median:.1f} ms, "
          f"best {min(samples):.1f}, max {max(samples):.1f}  budget +10% vs v43, ceiling {CREATE_SALE_CEILING_MS} ms")
    assert median <= CREATE_SALE_CEILING_MS, samples
    # The sale really moved the figure the way the writers promise (254 + 10).
    conn = get_retail_conn()
    try:
        bal = party_balance.customer_balance(conn, cid, ids[1], 'JOD')
        stored = conn.execute("SELECT credit_balance FROM customers WHERE id=?", (ids[1],)).fetchone()[0]
    finally:
        conn.close()
    assert float(bal) == pytest.approx(264.0, abs=0.001) and stored == pytest.approx(264.0, abs=0.001), (bal, stored)
