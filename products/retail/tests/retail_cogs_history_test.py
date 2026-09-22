"""
Aura Retail -- schema v39 cost-basis coverage for core/retail/metrics.py.

This is the follow-up metrics.py's own "KNOWN GAP -- COST AT TIME OF SALE"
section promised and database/schema.py's v39 migration (_migrate_add_cost_
basis) cashed in: `cogs()`, `top_products()` and `inventory_value()` now
PREFER a per-line/per-product cost SNAPSHOT over `products.cost_price` READ
RIGHT NOW, and fall back to `cost_price` when there is no snapshot (a
pre-v39 row, or one where none was ever taken). See metrics.py's own
docstrings on those three functions for the full reasoning; this file
proves the READ side behaves the way those docstrings say it does.

THE HEADLINE DEFECT THIS CLOSES, restated as a test: before this fix,
`cogs()` joined `products.cost_price` on both legs, so raising a product's
cost price today retroactively shrank every PAST period's reported gross
profit. `test_v39_sale_reports_cogs_from_its_own_snapshot_and_ignores_a_
later_cost_price_edit` below reproduces exactly that -- sell, read the
figure, edit the price, read again -- and asserts the second read is
UNCHANGED. That is the one assertion in this file a reader should trust
least on faith and most on having watched it fail; see this file's own
verification notes (products/retail/tests/retail_cogs_history_test.py's
companion mutation proof, run by hand against a temporarily broken
metrics.py and reported in the change that introduced this file) for the
real RED output that assertion produces the moment the fallback --
or the snapshot itself -- is removed.

HARNESS: same shape as retail_metrics_business_date_test.py's own -- a
hand-built sqlite file carrying exactly the columns metrics.py reads (base
DDL plus the real v13 identity/attribution columns via schema.py's own
migration function, called directly rather than hand-copied, so a future
rename of created_at_utc breaks this fixture instead of silently testing a
column metrics.py no longer reads), NOT the full init_retail() chain. No
Flask app anywhere in this file: retail_api.py does not yet write `sale_
items.unit_cost` at all (grepped before writing this file -- zero INSERT
INTO sale_items mentions the column), so a route-level fixture could not
exercise the snapshot path in the first place. Direct SQL is not a
shortcut here, it is the only way to construct the rows this file needs to
exist.

CRITICAL: exactly ONE pytest process per file (AUDIT-010) -- these test
files pollute each other in a shared pytest process; see products/
run_all_tests.py.

Run:
    pytest products/retail/tests/retail_cogs_history_test.py -v
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# AURA_APP_DATA is set only so importing config/schema is well-defined --
# this file builds its own sqlite file directly and never calls
# init_retail(), the identical posture retail_metrics_business_date_test.py
# already takes and for the same reason (immunity from run_all_tests.py's
# module-level AURA_APP_DATA caching across sibling files).
DATA = Path(tempfile.mkdtemp(prefix="aura_retail_cogs_hist_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from core.retail import metrics  # noqa: E402
from database import schema  # noqa: E402

CID = 91001
BRANCH = 1


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── fixture ──────────────────────────────────────────────────────────────────
#
# Same base shape as retail_metrics_business_date_test.py's _BASE_DDL, with
# the two v39 columns added (sale_items.unit_cost, products.avg_cost) and
# products.deleted_at_utc added -- inventory_value()'s own WHERE clause
# names it, so it has to exist for that query to run at all, not merely to
# be exercised.
#
# Schema v40 (per-branch cost basis) ADDS inventory_balances.avg_cost here
# too, for the identical "has to exist for the query to run at all" reason:
# metrics.inventory_value() now reads `b.avg_cost` before falling back to
# `p.avg_cost`/`p.cost_price` (see that function's own docstring), so this
# hand-built table needs the column even though no row below ever sets it.
# EVERY row in this fixture leaves it at the column default (0), so
# `NULLIF(b.avg_cost, 0)` is always NULL here and the fallback chain lands
# on exactly the same `p.avg_cost`/`p.cost_price` tier it did before v40 --
# `test_inventory_value_prefers_avg_cost_and_falls_back_to_cost_price` below
# is therefore unchanged BYTE FOR BYTE, same assertion, same expected
# figures. What it can no longer be read as covering: it does NOT exercise
# the new branch-level tier of the fallback chain at all (a row with a real,
# nonzero `b.avg_cost` that should win over `p.avg_cost`) -- that property
# is proven instead in retail_v40_branch_cost_test.py, which drives real
# branches through the real HTTP routes rather than a hand-built row.

_BASE_DDL = """
CREATE TABLE branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1,
    name TEXT NOT NULL, address TEXT, phone TEXT, status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE products (
    id TEXT PRIMARY KEY, company_id INTEGER DEFAULT 1, sku TEXT NOT NULL, barcode TEXT,
    name TEXT NOT NULL, category_id TEXT, cost_price REAL DEFAULT 0, avg_cost REAL DEFAULT 0,
    sell_price REAL DEFAULT 0, tax_rate REAL DEFAULT 0, unit TEXT DEFAULT 'pcs',
    reorder_level INTEGER DEFAULT 5, status TEXT DEFAULT 'active', deleted_at_utc TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE inventory_balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1,
    product_id TEXT NOT NULL, branch_id INTEGER NOT NULL,
    quantity_on_hand REAL DEFAULT 0, quantity_reserved REAL DEFAULT 0,
    avg_cost REAL DEFAULT 0,
    UNIQUE(company_id, product_id, branch_id));
CREATE TABLE sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, sale_number TEXT UNIQUE,
    branch_id INTEGER, customer_id INTEGER, cashier TEXT DEFAULT 'POS',
    subtotal REAL DEFAULT 0, discount_amount REAL DEFAULT 0, tax_amount REAL DEFAULT 0,
    total REAL DEFAULT 0, amount_paid REAL DEFAULT 0, change_amount REAL DEFAULT 0,
    payment_method TEXT DEFAULT 'cash', status TEXT DEFAULT 'completed',
    idempotency_key TEXT UNIQUE, notes TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE sale_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, sale_id INTEGER NOT NULL, product_id TEXT NOT NULL,
    quantity REAL NOT NULL, unit_price REAL NOT NULL, discount_pct REAL DEFAULT 0,
    tax_rate REAL DEFAULT 0, line_total REAL NOT NULL, unit_cost REAL DEFAULT 0);
CREATE TABLE returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, return_number TEXT UNIQUE,
    sale_id INTEGER, branch_id INTEGER, cashier TEXT DEFAULT 'POS', reason TEXT,
    refund_method TEXT DEFAULT 'cash', refund_amount REAL DEFAULT 0,
    status TEXT DEFAULT 'completed', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE return_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, return_id INTEGER NOT NULL, product_id TEXT NOT NULL,
    quantity REAL NOT NULL, unit_price REAL NOT NULL, line_total REAL NOT NULL);
CREATE TABLE retail_settings (
    company_id INTEGER, skey TEXT, svalue TEXT, PRIMARY KEY (company_id, skey));
"""


def _new_db():
    """A retail.db carrying the real v13 identity/attribution columns
    (created_at_utc, actor_user_uid, terminal_id) -- called via the real
    migration function, not hand-copied, exactly as retail_metrics_
    business_date_test.py's own _new_db() does and for the same reason.
    Without created_at_utc existing as a column, metrics.py's `_local_ts()`
    (COALESCE(created_at, created_at_utc)) is a SQL error, not a degraded
    result -- this file's period bucketing has no zone configured (no rows
    in retail_settings), so `business_day()` returns UNCONFIGURED_BUSINESS_DAY
    and every date predicate runs on the plain `created_at` string, but the
    column still has to exist for the COALESCE to parse."""
    path = DATA / 'cogs_hist.db'
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_BASE_DDL)
    schema._migrate_add_identity_and_attribution_columns(conn)
    conn.commit()
    return conn


def _product(conn, pid, sku, cost_price, avg_cost=0.0):
    conn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,avg_cost,sell_price,tax_rate) "
        "VALUES (?,?,?,?,?,?,0,0)",
        (pid, CID, sku, sku, cost_price, avg_cost))


def _sale(conn, sale_no, pid, qty, unit_price, unit_cost, day):
    """One sale, one line. `unit_cost` is the schema-v39 snapshot -- 0.0
    reproduces a pre-v39 row exactly (the migration's own DEFAULT and its
    documented "no snapshot was taken" meaning), a nonzero value reproduces
    a sale rung after create_sale() starts writing it."""
    total = round(qty * unit_price, 2)
    cur = conn.execute(
        "INSERT INTO sales (company_id,sale_number,branch_id,total,amount_paid,"
        "payment_method,status,created_at) VALUES (?,?,?,?,?,'cash','completed',?)",
        (CID, sale_no, BRANCH, total, total, f'{day} 10:00:00'))
    sale_id = cur.lastrowid
    conn.execute(
        "INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,discount_pct,"
        "tax_rate,line_total,unit_cost) VALUES (?,?,?,?,0,0,?,?)",
        (sale_id, pid, qty, unit_price, total, unit_cost))
    return sale_id


def _return(conn, ret_no, sale_id, pid, qty, unit_price, day):
    """A return against `sale_id`. return_items carries no cost column of
    its own (real schema, see schema.py's return_items CREATE TABLE) --
    that absence is the entire reason cogs()'s returned leg has to look the
    original sale_items row back up rather than reading one off this row."""
    amount = round(qty * unit_price, 2)
    cur = conn.execute(
        "INSERT INTO returns (company_id,return_number,sale_id,branch_id,refund_method,"
        "refund_amount,status,created_at) VALUES (?,?,?,?,'cash',?,'completed',?)",
        (CID, ret_no, sale_id, BRANCH, amount, f'{day} 11:00:00'))
    ret_id = cur.lastrowid
    conn.execute(
        "INSERT INTO return_items (return_id,product_id,quantity,unit_price,line_total) "
        "VALUES (?,?,?,?,?)",
        (ret_id, pid, qty, unit_price, amount))
    return ret_id


def _day(text):
    return metrics.period_for_day(text)


# Every scenario below owns its own product id(s) and its own calendar day,
# so mutating one scenario's data (the cost_price edit in the headline test)
# can never leak into another scenario's expected figures -- the period
# predicate alone keeps them apart even though every row lives in one
# connection and one company.
DAY_V39 = '2026-05-01'
DAY_LEGACY = '2026-05-02'
DAY_MIX = '2026-05-03'
DAY_RET_V39 = '2026-05-04'
DAY_RET_LEGACY = '2026-05-05'
DAY_AGREE = '2026-05-06'


@pytest.fixture(scope="module")
def db():
    conn = _new_db()

    # -- scenario 1/2: a v39 snapshot, later contradicted by cost_price --
    # cost_price (8.00) is DELIBERATELY different from the snapshot (5.00)
    # from the start: if they coincided, a bug that silently fell back to
    # cost_price would produce the same number and go undetected.
    _product(conn, 'p-v39', 'SKU-V39', cost_price=8.00)
    _sale(conn, 'S-V39', 'p-v39', qty=3, unit_price=20.00, unit_cost=5.00, day=DAY_V39)

    # -- scenario 3: pre-v39 row, no snapshot at all --
    _product(conn, 'p-legacy', 'SKU-LEGACY', cost_price=7.00)
    _sale(conn, 'S-LEGACY', 'p-legacy', qty=5, unit_price=10.00, unit_cost=0.0, day=DAY_LEGACY)

    # -- scenario 4: one snapshot line and one fallback line, same period --
    _product(conn, 'p-mix-v39', 'SKU-MIX-V39', cost_price=9.00)
    _sale(conn, 'S-MIX-V39', 'p-mix-v39', qty=3, unit_price=25.00, unit_cost=5.00, day=DAY_MIX)
    _product(conn, 'p-mix-legacy', 'SKU-MIX-LEGACY', cost_price=7.00)
    _sale(conn, 'S-MIX-LEGACY', 'p-mix-legacy', qty=2, unit_price=12.00, unit_cost=0.0, day=DAY_MIX)

    # -- scenario 5: a v39 sale, returned in full, in the same period --
    _product(conn, 'p-ret-v39', 'SKU-RET-V39', cost_price=8.00)
    sid = _sale(conn, 'S-RET-V39', 'p-ret-v39', qty=4, unit_price=20.00, unit_cost=5.00, day=DAY_RET_V39)
    _return(conn, 'R-RET-V39', sid, 'p-ret-v39', qty=4, unit_price=20.00, day=DAY_RET_V39)

    # -- scenario 6: a pre-v39 sale, returned in full --
    _product(conn, 'p-ret-legacy', 'SKU-RET-LEGACY', cost_price=6.00)
    sid = _sale(conn, 'S-RET-LEGACY', 'p-ret-legacy', qty=2, unit_price=15.00, unit_cost=0.0, day=DAY_RET_LEGACY)
    _return(conn, 'R-RET-LEGACY', sid, 'p-ret-legacy', qty=2, unit_price=15.00, day=DAY_RET_LEGACY)

    # -- scenario 8: summary() and top_products() must agree --
    _product(conn, 'p-agree-a', 'SKU-AGREE-A', cost_price=10.00)
    sid = _sale(conn, 'S-AGREE-A', 'p-agree-a', qty=6, unit_price=15.00, unit_cost=6.00, day=DAY_AGREE)
    _return(conn, 'R-AGREE-A', sid, 'p-agree-a', qty=2, unit_price=15.00, day=DAY_AGREE)
    _product(conn, 'p-agree-b', 'SKU-AGREE-B', cost_price=4.00)
    _sale(conn, 'S-AGREE-B', 'p-agree-b', qty=5, unit_price=8.00, unit_cost=0.0, day=DAY_AGREE)

    # -- scenario 7: inventory_value() -- no period, company-wide, so these
    # two products must be the ONLY ones with an inventory_balances row in
    # this fixture, or the total below would pick up every other scenario's
    # stock too.
    _product(conn, 'p-inv-a', 'SKU-INV-A', cost_price=4.00, avg_cost=6.50)
    conn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) "
        "VALUES (?,?,?,10)", (CID, 'p-inv-a', BRANCH))
    _product(conn, 'p-inv-b', 'SKU-INV-B', cost_price=3.00, avg_cost=0.0)
    conn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) "
        "VALUES (?,?,?,4)", (CID, 'p-inv-b', BRANCH))

    conn.commit()
    yield conn
    conn.close()


# ── 1/2. the headline defect: snapshot preferred, immune to a later edit ──────

def test_v39_sale_reports_cogs_from_its_own_snapshot_and_ignores_a_later_cost_price_edit(db):
    """THE fix, read straight off the two figures a shopkeeper actually
    sees. First: a sale whose line carries a snapshot (unit_cost=5.00) must
    report COGS from that snapshot (15.00 for 3 units), not from
    cost_price (8.00, which would be 24.00). Second, and this is the
    assertion that matters most: editing cost_price AFTER the sale --
    exactly what a shopkeeper does when a supplier's price changes -- must
    NOT move either figure. Before this fix it always did; see this file's
    module docstring for where the real mutation-proof RED output for this
    exact assertion is reported."""
    period = _day(DAY_V39)
    assert metrics.cogs(db, CID, period) == 15.00
    card = metrics.summary(db, CID, period)
    assert card['revenue'] == 60.00
    assert card['cogs'] == 15.00
    assert card['gross_profit'] == 45.00

    db.execute("UPDATE products SET cost_price=99.00 WHERE id='p-v39'")
    db.commit()

    # Unchanged. A pre-fix implementation reading cost_price on this leg
    # would report cogs=3*99=297.00 and a NEGATIVE gross_profit here.
    assert metrics.cogs(db, CID, period) == 15.00
    card_after = metrics.summary(db, CID, period)
    assert card_after['gross_profit'] == 45.00
    assert card_after == card, (
        "the ENTIRE summary card must be byte-identical after a cost_price "
        "edit on a product whose sale carries its own snapshot"
    )


# ── 3. no regression: a pre-v39 row still prices from cost_price ──────────────

def test_pre_v39_sale_still_reports_cogs_from_cost_price(db):
    """A row with unit_cost=0 means "no snapshot was taken", never "these
    goods were free". The wrong fix here -- reading the snapshot with no
    fallback -- collapses every pre-v39 sale's cost to zero and its margin
    to 100%, which is strictly worse than the defect this migration closes.
    See this file's module docstring for the real mutation-proof RED output
    this exact assertion produces when the fallback is removed."""
    period = _day(DAY_LEGACY)
    assert metrics.cogs(db, CID, period) == 35.00      # 5 units * cost_price 7.00, NOT 0
    card = metrics.summary(db, CID, period)
    assert card['revenue'] == 50.00
    assert card['cogs'] == 35.00
    assert card['gross_profit'] == 15.00                # NOT 50.00 (the 100%-margin collapse)


# ── 4. a mix of both bases in one period sums correctly ───────────────────────

def test_a_mix_of_snapshot_and_fallback_lines_in_one_period_sums_correctly(db):
    """Two products sold on the same day: one carries a real snapshot, the
    other does not. The CASE/COALESCE that picks the basis has to be
    evaluated PER ROW inside the SUM, not decided once for the whole query
    -- this is what would break if someone "simplified" the SQL to branch
    on some query-wide condition instead."""
    period = _day(DAY_MIX)
    # 3 units * unit_cost 5.00 (snapshot) + 2 units * cost_price 7.00 (fallback)
    assert metrics.cogs(db, CID, period) == 29.00
    card = metrics.summary(db, CID, period)
    assert card['revenue'] == 99.00
    assert card['gross_profit'] == 70.00


# ── 5. a returned v39 sale nets at the SNAPSHOT, not at cost_price ────────────

def test_return_of_a_v39_sale_nets_at_the_snapshot_cost(db):
    """cost_price (8.00) and the snapshot (5.00) disagree on purpose -- if
    the returned leg used cost_price instead of looking the original sale
    line back up, this full round trip would net to a NON-zero cost (a
    residue of pure price-edit noise) instead of the honest answer: the
    goods went out and came straight back, so their cost was never really
    consumed, and gross profit for the period must return to exactly what
    it was before any of this happened (0.00, nothing else occurred that
    day). See this file's module docstring for the real mutation-proof RED
    output this assertion produces when the returned leg is pointed at
    cost_price instead of the original sale line."""
    period = _day(DAY_RET_V39)
    assert metrics.cogs(db, CID, period) == 0.00
    card = metrics.summary(db, CID, period)
    assert card['revenue'] == 0.00       # sale and its full refund cancel
    assert card['gross_profit'] == 0.00


# ── 6. a returned pre-v39 sale still nets at cost_price ───────────────────────

def test_return_of_a_pre_v39_sale_still_nets_at_cost_price(db):
    """Both the sold leg and the returned-leg lookup resolve to unit_cost=0
    here (no snapshot on the original line either), so both fall back to
    `cost_price`, read at the same instant this one call executes -- the
    same "same expression, same column, one call" guarantee the pre-v39
    fallback rests on everywhere else in this module. Net cost is exactly
    zero for the same reason scenario 5's is, on the old basis rather than
    the new one."""
    period = _day(DAY_RET_LEGACY)
    assert metrics.cogs(db, CID, period) == 0.00
    card = metrics.summary(db, CID, period)
    assert card['revenue'] == 0.00
    assert card['gross_profit'] == 0.00


# ── 7. inventory_value() prefers avg_cost, falls back to cost_price ───────────

def test_inventory_value_prefers_avg_cost_and_falls_back_to_cost_price(db):
    """p-inv-a carries BOTH a cost_price (4.00) and an avg_cost (6.50) that
    disagree on purpose -- the read must be 10 * 6.50, not 10 * 4.00.
    p-inv-b carries avg_cost=0 (unset), so it must fall back to its
    cost_price (3.00) -- 4 * 3.00. No period, no branch filter: this is a
    company-wide snapshot, and these two products are the only ones this
    fixture gave an inventory_balances row, so the total is exactly the sum
    of the two."""
    assert metrics.inventory_value(db, CID) == 65.00 + 12.00


# ── 8. the summary card and the per-product profit column agree ──────────────

def test_summary_gross_profit_agrees_with_the_summed_top_products_profit(db):
    """This is the property core/retail/metrics.py's module docstring (#4)
    says the whole module exists to protect: the Reports summary card and
    the per-product breakdown beside it must never disagree. p-agree-a
    carries a v39 snapshot and a partial return; p-agree-b is a plain
    pre-v39 fallback line. If top_products() had been left computing cost
    from cost_price alone while cogs() moved to the snapshot basis, this
    assertion is exactly what would have caught it -- untruncated (limit
    high enough for both products), the two independently-computed figures
    must land on the same number."""
    period = _day(DAY_AGREE)
    card = metrics.summary(db, CID, period)
    rows = metrics.top_products(db, CID, period, limit=50)
    assert len(rows) == 2

    by_sku = {r['sku']: r for r in rows}
    # p-agree-a: sold 6 * unit_cost 6.00 = 36.00, returned 2 * (same
    # snapshot) 6.00 = 12.00, net cost 24.00; revenue (6-2)*15.00 = 60.00.
    assert by_sku['SKU-AGREE-A']['cost'] == 24.00
    assert by_sku['SKU-AGREE-A']['revenue'] == 60.00
    assert by_sku['SKU-AGREE-A']['profit'] == 36.00
    # p-agree-b: sold 5 * cost_price 4.00 (fallback) = 20.00; revenue 40.00.
    assert by_sku['SKU-AGREE-B']['cost'] == 20.00
    assert by_sku['SKU-AGREE-B']['revenue'] == 40.00
    assert by_sku['SKU-AGREE-B']['profit'] == 20.00

    total_profit = round(sum(r['profit'] for r in rows), 2)
    total_cost = round(sum(r['cost'] for r in rows), 2)
    assert card['gross_profit'] == 56.00
    assert card['gross_profit'] == total_profit, (
        f"summary()'s gross_profit ({card['gross_profit']}) must equal the sum of "
        f"top_products()'s per-product profit ({total_profit}) -- this is the exact "
        f"cross-screen disagreement core/retail/metrics.py exists to prevent"
    )
    assert card['cogs'] == total_cost
