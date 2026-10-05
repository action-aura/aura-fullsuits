"""Aura Today (TA step 4) -- what GET /reports/today costs on a busy shop.

The shop is the v44 verifier's: 500 debtors, 20,000 sales (15,000 on credit,
5,000 walk-in cash), 4,000 account payments, 3,000 returns -- plus 300
cheques and 200 products at or below their reorder level, which the party
balance fixture does not have. Fixture code is copied from
retail_party_balance_perf_test.py (a test file cannot import another's
module-level boot).

  PATH                      BUDGET (plan, decision 11)   CEILING ASSERTED
  GET /reports/today        <= 300 ms best-of-3          <= 600 ms

OPT-IN (AURA_RUN_PERF=1), for the reason the party-balance file gives: wall-
clock timings are meaningless inside the parallel suite. The Server-Timing
header is PRINTED so a miss says which section is slow.

Run (alone, quiet machine):
    AURA_RUN_PERF=1 python -m pytest products/retail/tests/retail_today_perf_test.py -v -s
"""
import os
import random
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

if os.environ.get('AURA_RUN_PERF') != '1':
    pytest.skip('timing benchmark: opt-in with AURA_RUN_PERF=1 on a quiet machine '
                '(see module docstring)', allow_module_level=True)

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_today_perf_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
                  AURA_SITE_RELAY_ENABLED="0")
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
CREDIT_SALES_PER_DEBTOR = 30
N_CASH_SALES = 5000
PAYMENTS_PER_DEBTOR = 8
RETURNS_PER_DEBTOR = 6
N_CHEQUES = 300
N_LOW_STOCK = 200

BUDGET_MS = 300
CEILING_MS = 600


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
    email = f"todayperf-{uuid.uuid4().hex[:10]}@test.local"
    password = "TodayPerfPW1"
    cid = str(uuid.uuid4())
    reg = registry_conn()
    reg.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), cid, "EMP-0001", email, hash_password(password), "admin", "active"))
    reg.commit(); reg.close()

    conn = get_retail_conn()
    for name in ('Main', 'Second'):
        conn.execute("INSERT INTO branches (company_id,name) VALUES (?, ?)", (cid, name))
    bids = [r[0] for r in conn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id", (cid,)).fetchall()]
    bid = bids[0]
    conn.execute("INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                 (cid, 'business_timezone', 'Asia/Amman'))
    conn.commit(); conn.close()

    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    client.get(f"{API}/settings/tax")
    r = client.post(f"{API}/settings/credit", json={"base_currency": "JOD"})
    assert r.status_code == 200, r.get_json()

    conn = get_retail_conn()
    for doc_type in ('sale', 'return'):
        conn.execute("INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
                     (cid, doc_type, random.randint(1, 5_000_000)))

    # "now" is the REAL clock here (the route is not frozen), so the fixture is
    # laid out relative to it and today's own sales are real.
    now = datetime.now(timezone.utc).replace(microsecond=0)

    def stamp(days):
        t = now - timedelta(days=days)
        return (t + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S'), t.strftime('%Y-%m-%d %H:%M:%S')

    debtors, sales, pays, rets = [], [], [], []
    n = 0
    for i in range(N_DEBTORS):
        debtors.append((str(uuid.uuid4()), cid, f'Perf {i}', 'unlimited', 0, 0))
    conn.executemany("INSERT INTO customers (id,company_id,name,credit_mode,credit_limit,credit_balance) "
                     "VALUES (?,?,?,?,?,?)", debtors)
    for (c, *_r) in debtors:
        for j in range(CREDIT_SALES_PER_DEBTOR):
            n += 1
            ca, cu = stamp(j * 3)
            sales.append((cid, f'PS-{n}-{uuid.uuid4().hex[:6]}', bid, c, 10.0, 10.0, 0, 'credit',
                          'completed', ca, cu))
        for j in range(PAYMENTS_PER_DEBTOR):
            ca, _cu = stamp(10 + j * 10)
            pays.append((cid, f'PP-{uuid.uuid4().hex[:10]}', 'customer', c, 'in', 5.0, 'JOD', 1, 'cash',
                         'active', None, ca))
    for k in range(N_CASH_SALES):
        n += 1
        ca, cu = stamp(k % 300)
        sales.append((cid, f'PS-{n}-{uuid.uuid4().hex[:6]}', bids[k % 2], None, 3.0, 3.0, 3.0, 'cash',
                      'completed', ca, cu))
    conn.executemany(
        "INSERT INTO sales (company_id, sale_number, branch_id, customer_id, subtotal, total, amount_paid, "
        "payment_method, status, created_at, created_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?)", sales)
    conn.executemany(
        "INSERT INTO payments (company_id, reference, party_type, party_id, direction, amount, currency, fx_rate, "
        "method, status, related_type, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", pays)
    sale_ids = {}
    for sid, cust in conn.execute(
            "SELECT id, customer_id FROM sales WHERE company_id=? AND customer_id IS NOT NULL", (cid,)).fetchall():
        sale_ids.setdefault(cust, []).append(sid)
    for (c, *_r) in debtors:
        for j, sid in enumerate(sale_ids[c][:RETURNS_PER_DEBTOR]):
            ca, cu = stamp(2 + j)
            rets.append((cid, f'PR-{uuid.uuid4().hex[:10]}', sid, bid, 1.0, 'completed', ca, cu, 1.0, 0.0, 0))
    conn.executemany(
        "INSERT INTO returns (company_id, return_number, sale_id, branch_id, refund_amount, status, created_at, "
        "created_at_utc, ar_forgiven_amount, store_credit_amount, tender_refund_amount) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rets)

    # cheques (every one 'pending': a created event each) and low-stock products
    cust0 = debtors[0][0]
    today = datetime.now().date()
    for k in range(N_CHEQUES):
        chq = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO cheques (id, company_id, branch_id, direction, party_type, party_id, cheque_number, "
            "bank_name, amount, currency, due_date, created_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (chq, cid, bid, 'in' if k % 4 else 'out', 'customer', cust0, f'CH{k}', 'Arab Bank', 50.0, 'JOD',
             (today + timedelta(days=(k % 20) - 5)).isoformat(), '2026-10-01T08:00:00+00:00'))
        conn.execute(
            "INSERT INTO cheque_events (id, cheque_id, event_type, from_status, to_status, created_at_utc) "
            "VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), chq, 'created', None, 'pending', '2026-10-01T08:00:00+00:00'))
    for k in range(N_LOW_STOCK):
        pid = str(uuid.uuid4())
        conn.execute("INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate,reorder_level) "
                     "VALUES (?,?,?,?,?,?,0,?)", (pid, cid, f'LS-{k}', f'Low {k}', 2.0, 4.0, 10))
        conn.execute("INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) "
                     "VALUES (?,?,?,?)", (cid, pid, bids[k % 2], k % 10))
    conn.commit()
    party_balance.refresh_cache(conn, cid, 'customers', currency='JOD')
    conn.commit()
    n_sales = conn.execute("SELECT COUNT(*) FROM sales WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()
    print(f"\nPERF_FIXTURE {N_DEBTORS} debtors / {n_sales} sales / {N_CHEQUES} cheques / {N_LOW_STOCK} low-stock")
    return client


def test_today_route_on_a_busy_shop(shop):
    client = shop
    r = client.get(f"{API}/reports/today")                              # warm
    assert r.status_code == 200, r.get_data(as_text=True)
    timings = _best(lambda: client.get(f"{API}/reports/today"))
    r = client.get(f"{API}/reports/today")
    print(f"PERF /reports/today: {[round(t) for t in timings]} ms (best {round(min(timings))})  "
          f"budget {BUDGET_MS} ms, ceiling {CEILING_MS} ms")
    print(f"PERF Server-Timing: {r.headers['Server-Timing']}")
    body = r.get_json()['data']
    assert body['cards'], "the busy fixture must open cards, or this timed an empty path"
    assert min(timings) <= CEILING_MS, (timings, r.headers['Server-Timing'])
