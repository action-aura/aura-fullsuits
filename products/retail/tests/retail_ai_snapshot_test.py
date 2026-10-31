"""Aura Retail -- the AI snapshot: right figures, tenant-isolated, and NEVER a
customer's name, phone or email (spec R6), within a hard size budget.

What each test guards, and the direction that proves it is not vacuous:

  * figures        -- a seeded shop reads back exactly the revenue, top
                      product, stock lists and product pairs it was built with
                      (and a sale OUTSIDE the 30-day window is not counted).
  * isolation      -- a second company's product, sale and customer leave no
                      trace in the first company's snapshot.
  * no customer PII-- distinctive names/phones/emails are seeded and searched
                      for in the SERIALIZED snapshot; the anonymous C1.. labels
                      and their spend/visits must be present, so the test
                      cannot pass merely because the customer block is empty.
  * size           -- the default budget is honoured on a deliberately fat
                      catalogue, AND a tiny budget makes the trimmer actually
                      remove rows (so "under 40 KB" is not true by accident of
                      a small fixture).
  * clock          -- no bare datetime.now() builds a window: the only wall-clock
                      read is the `now is None` fallback, and an injected `now`
                      fully decides the window.

Run:
    pytest products/retail/tests/retail_ai_snapshot_test.py -v
"""
import ast
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_ai_snapshot_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from core.retail import ai_snapshot  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

#: The shop's "now". Fixed so every window is deterministic.
NOW = datetime(2026, 10, 20, 14, 0, 0)

CUSTOMER_NAME = 'Zubeida Quartermaine-Okafor'
CUSTOMER_PHONE = '0799123456'
CUSTOMER_EMAIL = 'zubeida.q@privatemail.example'
CUSTOMER_ADDRESS = '17 Hidden Lane Secretville'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _stamp(days_ago):
    when = NOW - timedelta(days=days_ago)
    return when.strftime('%Y-%m-%d %H:%M:%S'), when.strftime('%Y-%m-%dT%H:%M:%S+00:00')


def _product(conn, cid, name, stock=None, reorder=5, branch=1, cost=1.0, price=2.0):
    pid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate,status,reorder_level) "
        "VALUES (?,?,?,?,?,?,0,'active',?)",
        (pid, cid, f'SKU-{uuid.uuid4().hex[:8]}', name, cost, price, reorder))
    if stock is not None:
        conn.execute("INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) "
                     "VALUES (?,?,?,?)", (cid, pid, branch, stock))
    return pid


def _customer(conn, cid, name=CUSTOMER_NAME):
    # customers.id is a TEXT uuid since the UUID migration. It MUST be set explicitly: a NULL id
    # (sqlite allows it on a TEXT primary key) would leave every sale pointing at a row that does not
    # exist, and the "no customer name leaks" test would then pass over an empty join -- the fixture
    # hiding the very state it is meant to exercise.
    customer_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO customers (id,company_id,name,phone,email,address,total_spent,created_at) "
        "VALUES (?,?,?,?,?,?,0,?)",
        (customer_id, cid, name, CUSTOMER_PHONE, CUSTOMER_EMAIL, CUSTOMER_ADDRESS, _stamp(3)[0]))
    return customer_id


def _sale(conn, cid, items, total, days_ago=1, customer_id=None, branch=1, method='cash', discount=0):
    created, created_utc = _stamp(days_ago)
    cur = conn.execute(
        "INSERT INTO sales (company_id,sale_number,branch_id,customer_id,cashier,subtotal,discount_amount,"
        "tax_amount,total,amount_paid,payment_method,status,created_at,created_at_utc,uid) "
        "VALUES (?,?,?,?,'POS',?,?,0,?,?,?,'completed',?,?,?)",
        (cid, f'AIS-{uuid.uuid4().hex[:12]}', branch, customer_id, total, discount, total, total,
         method, created, created_utc, str(uuid.uuid4())))
    sid = cur.lastrowid
    for pid, qty, unit_price in items:
        conn.execute(
            "INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,discount_pct,tax_rate,line_total) "
            "VALUES (?,?,?,?,0,0,?)", (sid, pid, qty, unit_price, qty * unit_price))
    return sid


def _shop():
    """A company with two products sold together twice, a low-stock product,
    a slow mover, one named customer with two visits, and one sale that is
    OUTSIDE the 30-day window."""
    cid = str(uuid.uuid4())
    conn = get_retail_conn()
    conn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (cid,))
    bread = _product(conn, cid, 'Aish Baladi Bread', stock=50, price=1.0)
    milk = _product(conn, cid, 'Fresh Milk 1L', stock=2, reorder=10, price=3.0)
    dust = _product(conn, cid, 'Dusty Lamp Shade', stock=40, price=10.0)
    cust = _customer(conn, cid)
    _sale(conn, cid, [(bread, 2, 1.0), (milk, 1, 3.0)], 5.0, days_ago=2, customer_id=cust)
    _sale(conn, cid, [(bread, 3, 1.0), (milk, 2, 3.0)], 9.0, days_ago=5, customer_id=cust, method='card',
          discount=0.5)
    _sale(conn, cid, [(bread, 1, 1.0)], 1.0, days_ago=7)
    _sale(conn, cid, [(dust, 1, 10.0)], 10.0, days_ago=60)          # outside the window
    conn.commit()
    conn.close()
    return {'cid': cid, 'bread': bread, 'milk': milk, 'dust': dust, 'cust': cust}


def _snap(cid, **kw):
    conn = get_retail_conn()
    try:
        return ai_snapshot.build_snapshot(conn, cid, now=NOW, **kw)
    finally:
        conn.close()


# ── figures ──────────────────────────────────────────────────────────────────

def test_figures_match_the_seeded_shop():
    shop = _shop()
    snap = _snap(shop['cid'])

    assert snap['currency'] == 'JOD'
    assert snap['as_of'] == '2026-10-20'
    # 5 + 9 + 1 in the window; the 60-day-old 10.0 sale is NOT counted.
    assert snap['summary']['revenue'] == 15.0
    assert snap['summary']['transactions'] == 3
    assert sum(r['revenue'] for r in snap['revenue_by_day']) == 15.0

    top = {p['name']: p for p in snap['top_products']}
    assert top['Aish Baladi Bread']['units'] == 6
    assert top['Fresh Milk 1L']['units'] == 3
    assert 'Dusty Lamp Shade' not in top
    assert snap['top_products'][0]['name'] == 'Aish Baladi Bread'

    # Stock lists.
    assert [r['name'] for r in snap['low_stock']] == ['Fresh Milk 1L']
    slow = {r['name']: r for r in snap['slow_movers']}
    assert slow['Dusty Lamp Shade']['units_sold_30d'] == 0
    assert slow['Dusty Lamp Shade']['on_hand'] == 40

    # Pairs: bread+milk bought together in 2 sales.
    pairs = {frozenset((p['a'], p['b'])): p['sales_together'] for p in snap['frequent_pairs']}
    assert pairs[frozenset(('Aish Baladi Bread', 'Fresh Milk 1L'))] == 2

    assert snap['discounts_total'] == 0.5
    assert {m['method'] for m in snap['payment_mix']} == {'cash', 'card'}
    assert sum(snap['revenue_by_weekday'].values()) == 15.0


def test_pairs_window_is_90_days_not_30():
    shop = _shop()
    conn = get_retail_conn()
    _sale(conn, shop['cid'], [(shop['bread'], 1, 1.0), (shop['dust'], 1, 10.0)], 11.0, days_ago=60)
    conn.commit()
    conn.close()
    snap = _snap(shop['cid'])
    pairs = {frozenset((p['a'], p['b'])) for p in snap['frequent_pairs']}
    assert frozenset(('Aish Baladi Bread', 'Dusty Lamp Shade')) in pairs
    assert snap['summary']['revenue'] == 15.0, 'the 60-day-old sale must not enter the 30-day figures'


def test_branch_scope_narrows_the_figures():
    shop = _shop()
    conn = get_retail_conn()
    other_branch = conn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Second')",
                                (shop['cid'],)).lastrowid
    _sale(conn, shop['cid'], [(shop['bread'], 1, 1.0)], 100.0, days_ago=1, branch=other_branch)
    conn.commit()
    conn.close()
    everything = _snap(shop['cid'])
    only_other = _snap(shop['cid'], branch_id=other_branch)
    assert everything['summary']['revenue'] == 115.0
    assert only_other['summary']['revenue'] == 100.0
    assert only_other['scope'] == 'branch' and everything['scope'] == 'all_branches'


# ── isolation ────────────────────────────────────────────────────────────────

def test_another_company_leaves_no_trace():
    mine = _shop()
    conn = get_retail_conn()
    theirs = str(uuid.uuid4())
    conn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Theirs')", (theirs,))
    secret = _product(conn, theirs, 'OTHERCO_SECRET_WIDGET', stock=0, reorder=100, price=500.0)
    spy_customer = _customer(conn, theirs, name='OTHERCO_SPY_CUSTOMER')
    _sale(conn, theirs, [(secret, 1, 500.0)], 500.0, days_ago=1, customer_id=spy_customer)
    conn.commit()
    conn.close()

    text = json.dumps(_snap(mine['cid']))
    assert 'OTHERCO' not in text
    assert _snap(mine['cid'])['summary']['revenue'] == 15.0
    # ...and symmetrically, the other company sees only its own.
    theirs_snap = _snap(theirs)
    assert theirs_snap['summary']['revenue'] == 500.0
    assert 'Aish Baladi' not in json.dumps(theirs_snap)


# ── R6: no customer identity ─────────────────────────────────────────────────

def test_no_customer_name_phone_email_or_address_anywhere():
    shop = _shop()
    text = json.dumps(_snap(shop['cid']), ensure_ascii=False)
    for needle in (CUSTOMER_NAME, 'Zubeida', 'Quartermaine', CUSTOMER_PHONE, CUSTOMER_EMAIL,
                   'privatemail', CUSTOMER_ADDRESS, 'Secretville'):
        assert needle not in text, f'customer identity leaked into the AI snapshot: {needle!r}'

    # ...and the anonymous block is genuinely populated (so the absence above
    # is not just an empty customer section).
    customers = _snap(shop['cid'])['customers']
    assert customers['total'] == 1 and customers['active_30d'] == 1
    assert customers['top'] == [{'label': 'C1', 'spend': 14.0, 'visits': 2}]
    assert customers['repeat_buyer_share_pct'] == 100.0
    assert customers['new_30d'] == 1


def test_customer_ids_are_not_in_the_snapshot_either():
    shop = _shop()
    entry = _snap(shop['cid'])['customers']['top'][0]
    assert set(entry) == {'label', 'spend', 'visits'}


# ── size ─────────────────────────────────────────────────────────────────────

def test_default_budget_holds_on_a_fat_catalogue():
    cid = str(uuid.uuid4())
    conn = get_retail_conn()
    conn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (cid,))
    pids = [_product(conn, cid, f'Product number {i:03d} ' + 'x' * 200, stock=i, reorder=1000)
            for i in range(120)]
    for i in range(0, 118):
        _sale(conn, cid, [(pids[i], 1, 2.0), (pids[i + 1], 1, 2.0), (pids[i + 2], 2, 2.0)], 8.0,
              days_ago=i % 29)
    conn.commit()
    conn.close()
    snap = _snap(cid)
    size = len(json.dumps(snap, ensure_ascii=False).encode('utf-8'))
    assert size <= ai_snapshot.SNAPSHOT_MAX_BYTES, size
    assert ai_snapshot.SNAPSHOT_MAX_BYTES == 40 * 1024


def test_a_tiny_budget_really_trims_rows():
    shop = _shop()
    full = _snap(shop['cid'])
    full_size = len(json.dumps(full, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    budget = full_size - 150
    conn = get_retail_conn()
    try:
        small = ai_snapshot.build_snapshot(conn, shop['cid'], now=NOW, max_bytes=budget)
    finally:
        conn.close()
    small_size = len(json.dumps(small, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    assert small_size <= budget, (small_size, budget)
    assert small_size < full_size
    # Trimmed from the least valuable list first: pairs go before top products.
    assert len(small['frequent_pairs']) < len(full['frequent_pairs']) or not full['frequent_pairs']
    assert small['summary'] == full['summary'], 'trimming must never touch the headline figures'


# ── clock ────────────────────────────────────────────────────────────────────

def test_only_the_now_none_fallback_reads_the_wall_clock():
    source = (BACKEND_DIR / 'core' / 'retail' / 'ai_snapshot.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    offenders = []

    def visit(node, guarded):
        for child in ast.iter_child_nodes(node):
            g = guarded
            if (isinstance(child, ast.If) and isinstance(child.test, ast.Compare)
                    and isinstance(child.test.left, ast.Name) and child.test.left.id == 'now'):
                g = True
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                    and child.func.attr in ('now', 'today', 'utcnow') and not guarded):
                offenders.append(ast.unparse(child))
            visit(child, g)

    visit(tree, False)
    assert not offenders, f'bare wall-clock reads outside the `now is None` fallback: {offenders}'


def test_an_injected_now_decides_the_window():
    shop = _shop()
    conn = get_retail_conn()
    try:
        later = ai_snapshot.build_snapshot(conn, shop['cid'], now=NOW + timedelta(days=40))
    finally:
        conn.close()
    assert later['as_of'] == '2026-11-29'
    assert later['summary']['revenue'] == 0, 'sales 33+ days before the injected now are out of window'
