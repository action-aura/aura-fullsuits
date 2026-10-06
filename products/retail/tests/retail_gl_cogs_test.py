"""Aura Retail -- schema v39 (cost basis + COGS) HTTP-driven proof.

The agent that wrote v39 (moving-average `products.avg_cost`, per-line
`sale_items.unit_cost` snapshot, `general_ledger.post_cogs`) was killed by a
network error before it wrote a single test. Everything this file exercises
was already IMPLEMENTED and, per this task's own audit, WORKING -- this file's
job is to PROVE it, HTTP-driven, the same level retail_gl_integration_test.py
already proves schema v38 at (real Flask routes, real `general_ledger.py`
reads, never a hand-built journal). One level below that: retail_gl_posting_
test.py already covers `post_journal`'s own balance invariant directly; this
file trusts that invariant and proves v39's NEW claims sit correctly on top
of it -- the moving average, the write-once snapshot, the COGS journal pair,
and the sync-payload coverage the same pass also touched.

THE CENTRAL CLAIM (the migration's own docstring, database/schema.py's
`_migrate_add_cost_basis`, calls it "the retroactive-profit defect"):
`sale_items.unit_cost` is a SNAPSHOT taken once, at the moment a sale rings,
and NEVER re-derived from `products.avg_cost`/`cost_price` as they stand
later. `test_later_cost_price_change_does_not_move_a_past_sales_unit_cost`
below is the one assertion in this file that matters most, and it is
mutation-proven (see this module's own verification notes, reported
alongside this file rather than duplicated here) against a REAL regression
that would reintroduce that exact defect at `GET /sales/<id>`.

Self-contained bootstrap, matching retail_gl_integration_test.py's own
convention (no shared conftest.py exists for products/retail/tests/). One
company per test via the `client` fixture, so balance/cost-basis assertions
never see another test's rows. `sync_outbox` is the one shared table this
file reads that carries no company_id column at all (see schema.py's own
CREATE TABLE) -- `_sync_payload`'s own docstring explains how it still reads
precisely in a multi-company, single-process test run.

CRITICAL: exactly ONE pytest process per file (AUDIT-010) -- these test
files pollute each other in a shared pytest process; see products/
run_all_tests.py.

Run:
    pytest products/retail/tests/retail_gl_cogs_test.py -q
"""
import json
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

DATA = Path(tempfile.mkdtemp(prefix="aura_gl_cogs_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from core.retail import general_ledger as gl  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_background_push(monkeypatch):
    """W4a-2 fix: receive_purchase_order now ends with `_sync_nudge()` (the
    receipt and its lines are queued), as sale/return/transfer routes already
    did. The nudge starts a background push that DRAINS `sync_outbox`, and
    several tests here read the outbox right after a route call (the sale_item /
    sale payload tests, and the product-update COUNT before/after test), so
    whether the row was still there was timing: whole-file run red, each test
    alone green (measured 2026-10-06, 3 failures; same shape as 26ad74a2).
    What this can no longer catch: a nudge thread -- not what these tests
    guard; they assert WHAT is queued, and stubbing changes nothing a route
    queues."""
    import api.retail_api as _ra
    monkeypatch.setattr(_ra, '_sync_nudge', lambda: None)


@pytest.fixture
def client():
    email = f'glcogs-{uuid.uuid4().hex[:8]}@test.local'
    password = 'GlCogsIntegrationPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'))
    conn.commit(); conn.close()

    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    c.test_company_id = company_id
    return c


# ── Seed helpers ─────────────────────────────────────────────────────────────

def _seed_product(c, **kw):
    # `initial_stock` defaults to 0, UNLIKE retail_gl_integration_test.py's
    # own `_seed_product` (which defaults 100) -- every weighted-average
    # assertion in this file hand-computes its expected result from
    # `qty_on_hand` at the moment of each receipt (see receive_purchase_
    # order's own comment: it is read COMPANY-WIDE, summed across every
    # branch), so a nonzero opening balance this file did not put there
    # would silently change every hand-computed number below.
    payload = {'name': 'COGS Widget', 'sku': f'COGSW-{uuid.uuid4().hex[:8]}', 'cost_price': 1.0,
               'sell_price': 10.0, 'tax_rate': 0, 'initial_stock': 0}
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_supplier(c):
    r = c.post(f'{API}/suppliers', json={'name': f'COGS Supplier {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_customer(c):
    r = c.post(f'{API}/customers', json={'name': f'COGS Customer {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _set_loyalty_point_value(c, value):
    r = c.post(f'{API}/settings/credit', json={'loyalty_point_value': value})
    assert r.status_code == 200, r.get_json()


def _grant_ledger_points(company_id, customer_id, points):
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,created_by) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, customer_id, points, 'adjust', 'test-fixture'))
    conn.commit(); conn.close()


def _create_po(c, supplier_id, pid, qty, unit_cost, amount_paid=0):
    r = c.post(f'{API}/purchase-orders', json={
        'supplier_id': supplier_id,
        'items': [{'product_id': pid, 'quantity': qty, 'unit_cost': unit_cost}],
        'amount_paid': amount_paid})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _receive_po(c, po_id):
    r = c.post(f'{API}/purchase-orders/{po_id}/receive')
    assert r.status_code == 200, r.get_json()


def _po_receipt(c, supplier_id, pid, qty, unit_cost, amount_paid=0):
    """Create + receive in one call. `products.avg_cost` is maintained ONLY
    by receive_purchase_order (see that route's own moving-average
    comment) -- create_purchase_order alone never moves it -- so every
    test in this file that needs a real cost basis goes through both
    steps, never just create."""
    po_id = _create_po(c, supplier_id, pid, qty, unit_cost, amount_paid)
    _receive_po(c, po_id)
    return po_id


# ── Read helpers (raw SQL, same posture as retail_gl_integration_test.py's
#    own `_customers_credit_sum`/`_suppliers_credit_sum`: reading the ledger
#    a route does not itself expose through a JSON response) ────────────────

def _product_cost(cid, pid):
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT avg_cost, cost_price FROM products WHERE id=? AND company_id=?",
            (pid, cid)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def _sale_item_rows(cid, sale_id):
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT si.* FROM sale_items si JOIN sales s ON si.sale_id = s.id "
            "WHERE s.id=? AND s.company_id=? ORDER BY si.id",
            (sale_id, cid)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _sale_uid(cid, sale_id):
    conn = get_retail_conn()
    try:
        return conn.execute(
            "SELECT uid FROM sales WHERE id=? AND company_id=?", (sale_id, cid)
        ).fetchone()['uid']
    finally:
        conn.close()


def _cogs_lines(cid, source_type, source_id):
    """Every gl_journal_lines row touching COGS (5900) or Inventory (1200)
    for ONE document (source_type, source_id) -- isolates post_cogs's own
    journal from post_sale's/post_return's SIBLING journal that shares the
    identical (source_type, source_id) key by design (see post_cogs's own
    docstring: `idx_gl_journals_source` carries no UNIQUE constraint on
    purpose, so one sale/return can own two journals). Empty means post_
    cogs posted NOTHING for this document at all -- the epsilon-gated "no
    known cost basis anywhere in this line" case, never a partial line
    that merely rounds small."""
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT a.code AS account_code, l.debit, l.credit "
            "FROM gl_journals j "
            "JOIN gl_journal_lines l ON l.journal_id = j.id "
            "JOIN gl_accounts a ON a.id = l.account_id "
            "WHERE j.company_id=? AND j.source_type=? AND j.source_id=? "
            "AND a.code IN (?,?)",
            (cid, source_type, source_id, gl.ACCOUNT_COGS, gl.ACCOUNT_INVENTORY)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _trial_balance(cid):
    conn = get_retail_conn()
    try:
        return gl.trial_balance(conn, cid)
    finally:
        conn.close()


def _sync_payload(entity_type, event_type, entity_id=None):
    """Latest queued `sync_outbox` row's PARSED payload for (entity_type,
    event_type[, entity_id]).

    `sync_outbox` carries no `company_id` column of its own (see schema.py's
    own CREATE TABLE) -- like every other table in this module, ALL of this
    file's test companies share ONE physical sqlite file across the whole
    pytest process, so a bare `WHERE entity_type=? AND event_type=?` alone
    would happily hand back another test's row. Precision comes from
    filtering by the row's own `entity_id` wherever the caller has one (a
    fresh uuid4 this test alone just minted -- product id, sale_item uid,
    sale uid -- can never collide with another test's), and otherwise from
    `rowid DESC` picking the event THIS test's own API call just queued,
    read immediately after making that call, with no other test running
    concurrently in this file (one pytest process, strictly sequential)."""
    conn = get_retail_conn()
    try:
        if entity_id is not None:
            row = conn.execute(
                "SELECT payload FROM sync_outbox WHERE entity_type=? AND event_type=? "
                "AND entity_id=? ORDER BY rowid DESC LIMIT 1",
                (entity_type, event_type, str(entity_id))
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT payload FROM sync_outbox WHERE entity_type=? AND event_type=? "
                "ORDER BY rowid DESC LIMIT 1",
                (entity_type, event_type)
            ).fetchone()
        return json.loads(row['payload']) if row else None
    finally:
        conn.close()


# ── Test-only direct writes -- see each call site's own comment for why no
#    route can produce the state being constructed ─────────────────────────

def _force_product_cost(cid, pid, avg_cost=None, cost_price=None):
    """Simulates 'a later cost edit' landing on a product AFTER a sale
    already rang against it. Two different real causes collapse into one
    helper: `cost_price` is PATCHable by a shopkeeper (PATCH /products/
    <id>) any time; `avg_cost` is NOT (only receive_purchase_order writes
    it -- see that route's own moving-average comment) so a real second
    receipt would be the only route-driven way to move it. Direct SQL
    stands in for 'a much bigger purchase arrived later' without needing a
    second real PO receipt to get there, and lets `avg_cost`/`cost_price`
    be pushed to the SAME deliberately-implausible figure so neither one
    can accidentally satisfy the test's assertion by coincidence.

    Schema v40 (per-branch cost basis) ADDS: when `avg_cost` is pushed,
    every `inventory_balances` row this product already has is pushed to
    the SAME figure too, not just `products.avg_cost`. Real production
    never lets these two disagree the way a bare `UPDATE products` alone
    now would -- create_sale reads a branch's OWN `inventory_balances.
    avg_cost` FIRST (see that route's own three-level fallback chain
    comment), falling through to `products.avg_cost` only when the branch
    figure is unset. Every scenario in this file has exactly one
    `inventory_balances` row per product (`_seed_product`'s own
    `initial_stock=0` seeds exactly one, on the one working branch), so a
    bare `WHERE company_id=? AND product_id=?` (no branch filter, mirroring
    `_force_negative_stock`'s identical company-wide UPDATE immediately
    below) always lands on that SAME single row, unambiguously. Without
    this, `test_receipt_that_would_go_negative_never_reaches_a_sale_
    snapshot_negative` failed with a REAL defect this file's own fixture
    was masking, not exposing: the branch-level average, never pushed to
    match the forced company-wide figure, computed its own (wrong) weighted
    average from a stale branch-local starting point of 0 instead of the
    forced 10.0, and a later sale snapshotted THAT wrong figure instead of
    the correctly-guarded company-wide fallback. Confirmed by running this
    file before this fix landed and reading the failure."""
    conn = get_retail_conn()
    sets, vals = [], []
    if avg_cost is not None:
        sets.append('avg_cost=?'); vals.append(avg_cost)
    if cost_price is not None:
        sets.append('cost_price=?'); vals.append(cost_price)
    vals += [pid, cid]
    conn.execute(f"UPDATE products SET {', '.join(sets)} WHERE id=? AND company_id=?", vals)
    if avg_cost is not None:
        conn.execute(
            "UPDATE inventory_balances SET avg_cost=? WHERE company_id=? AND product_id=?",
            (avg_cost, cid, pid))
    conn.commit(); conn.close()


def _force_sale_item_unit_cost(sale_item_id, unit_cost):
    """Simulates a PRE-v39 `sale_items` row, whose `unit_cost` sits at its
    column DEFAULT 0 because it predates the migration that started
    snapshotting one at all -- database/schema.py's `_migrate_add_cost_
    basis` docstring: "sale_items.unit_cost is DELIBERATELY NOT
    BACKFILLED... 0 here... is 'no snapshot was taken', not 'this sale
    cost nothing'." No route can produce this shape today (every sale
    create_sale writes NOW always snapshots a real cost_basis, even if
    that basis is itself 0 -- see test_sale_with_unknown_cost_basis... for
    THAT case), so direct SQL is the only way to build the row this test
    needs: a line that DOES have a real cost basis on its product today,
    but whose OWN historical snapshot is the pre-v39 sentinel."""
    conn = get_retail_conn()
    conn.execute("UPDATE sale_items SET unit_cost=? WHERE id=?", (unit_cost, sale_item_id))
    conn.commit(); conn.close()


def _force_negative_stock(cid, pid, qty_on_hand):
    """Simulates a company-wide `qty_on_hand` that has already gone
    negative -- the real-world cause is create_sale's own `behind_on_
    sync` oversell path (a device selling stock it has not yet learned
    about from a peer), which this file does not need a second real
    device to reproduce: receive_purchase_order reads `qty_on_hand` with
    a bare company-wide `SUM(quantity_on_hand)` (see that route's own
    comment), so forcing the SAME number directly into `inventory_
    balances` is indistinguishable, to the code under test, from a real
    oversell having produced it. `_seed_product`'s own `INSERT OR IGNORE
    ... VALUES (?,?,?,?)` (initial_stock, default 0) already guarantees
    exactly one `inventory_balances` row exists for a freshly seeded
    product on its one working branch, so a bare UPDATE (no INSERT OR
    IGNORE needed here) always has a row to land on."""
    conn = get_retail_conn()
    conn.execute(
        "UPDATE inventory_balances SET quantity_on_hand=? WHERE company_id=? AND product_id=?",
        (qty_on_hand, cid, pid))
    conn.commit(); conn.close()


def _negative_cost_counts(cid):
    """Company-scoped counts of any `products.avg_cost` / `sale_items.
    unit_cost` row that has gone negative -- the DEFECT 1 symptom,
    asserted as a property directly against the database rather than
    against one hand-picked number, so a fix that merely happens to dodge
    THIS test's own arithmetic cannot pass by accident. Scoped to `cid`
    (never a bare table-wide SELECT) because this file's own module
    docstring notes every OTHER test's company shares the same physical
    sqlite file across the whole pytest process."""
    conn = get_retail_conn()
    try:
        neg_avg = conn.execute(
            "SELECT COUNT(*) FROM products WHERE company_id=? AND avg_cost<0", (cid,)
        ).fetchone()[0]
        neg_unit_cost = conn.execute(
            "SELECT COUNT(*) FROM sale_items si JOIN sales s ON si.sale_id=s.id "
            "WHERE s.company_id=? AND si.unit_cost<0", (cid,)
        ).fetchone()[0]
        return {'avg_cost': neg_avg, 'unit_cost': neg_unit_cost}
    finally:
        conn.close()


def _product_update_event_count(pid):
    """How many `product`/`update` rows this product's id has queued into
    `sync_outbox` so far -- used as a BEFORE/AFTER diff (never a bare
    presence check) to prove a no-op receipt queues no SPURIOUS event, the
    same before/after-count posture retail_stock_accuracy_test.py already
    uses elsewhere in this suite for an identical "must NOT have grown"
    claim."""
    conn = get_retail_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM sync_outbox WHERE entity_type='product' AND event_type='update' "
            "AND entity_id=?", (str(pid),)
        ).fetchone()[0]
    finally:
        conn.close()


# ── 1. First-ever receipt: the moving average degenerates to received cost ──

def test_po_receipt_sets_avg_cost_on_a_product_with_none(client):
    """qty_on_hand is 0 at this product's first-ever receipt (`_seed_
    product`'s own zero `initial_stock`), so the moving-average formula
    collapses to exactly the received unit cost -- retail_api.py's own
    comment names this case explicitly ("the first receipt simply sets
    the average to the received cost, no special-casing needed"). Proves
    that degenerate case actually holds, not just that avg_cost becomes
    nonzero."""
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client)
    assert not _product_cost(cid, pid)['avg_cost']  # brand new, no PO receipt yet

    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0)

    assert _product_cost(cid, pid)['avg_cost'] == 4.0


# ── 2. Second receipt at a different price: THE formula's only real proof ──

def test_second_receipt_at_a_different_price_yields_the_exact_weighted_average(client):
    """Hand-computed expected value, asserted EXACTLY -- per this task's own
    instruction, an assertion like "it changed" or "it landed between 4.00
    and 10.00" would pass for a WRONG formula too (a plain mean of the two
    PRICES, for instance). 10 units already on hand @ 4.00, 5 units just
    received @ 10.00:

        new_avg = (10*4.00 + 5*10.00) / (10+5) = (40+50)/15 = 90/15 = 6.00

    A plain mean of the two prices would give (4.00+10.00)/2 = 7.00 --
    deliberately DIFFERENT from the correct 6.00 here (mutation (d) in this
    file's own verification notes forces exactly that wrong formula and
    reports this test going RED against 7.00)."""
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client)
    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0)
    assert _product_cost(cid, pid)['avg_cost'] == 4.0

    _po_receipt(client, supp_id, pid, qty=5, unit_cost=10.0)

    assert _product_cost(cid, pid)['avg_cost'] == 6.0


# ── 3. A sale snapshots unit_cost from avg_cost at that moment ──────────────

def test_sale_snapshots_unit_cost_from_avg_cost_at_that_moment(client):
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=50.0, tax_rate=0)
    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0)
    _po_receipt(client, supp_id, pid, qty=5, unit_cost=10.0)  # avg_cost -> 6.00 (test 2)
    assert _product_cost(cid, pid)['avg_cost'] == 6.0

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}],
        'payment_method': 'cash', 'amount_paid': 100.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']

    items = _sale_item_rows(cid, sale_id)
    assert len(items) == 1
    assert items[0]['unit_cost'] == 6.0


# ── 4. THE HEADLINE: a later cost edit does not move a past sale's snapshot ─

def test_later_cost_price_change_does_not_move_a_past_sales_unit_cost(client):
    """See this file's module docstring for why this is the one assertion
    here that matters most. Verified through the REAL `GET /sales/<id>`
    route -- deliberately NOT a raw SQL re-read of the same `sale_items`
    row, which could never go red no matter what create_sale did (nothing
    in this codebase ever UPDATEs `sale_items.unit_cost` after its own
    INSERT, so a same-row re-read is tautological). Reading back through
    the real route is what makes this test capable of catching a REAL
    regression -- a "fix" that makes `GET /sales/<id>` report a product's
    CURRENT cost instead of trusting the stored snapshot -- see this
    file's own mutation-proof notes for exactly that mutation, applied and
    reverted against get_sale's own SQL."""
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=50.0, tax_rate=0)
    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0)  # avg_cost -> 4.00

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 50.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']

    # A much later, much bigger purchase -- and a manual cost_price edit --
    # both land AFTER this sale already rang. See _force_product_cost's own
    # docstring for why direct SQL stands in for a second real PO receipt.
    _force_product_cost(cid, pid, avg_cost=500.0, cost_price=500.0)
    assert _product_cost(cid, pid)['avg_cost'] == 500.0  # the edit really landed

    r = client.get(f'{API}/sales/{sale_id}')
    assert r.status_code == 200, r.get_json()
    item = r.get_json()['data']['items'][0]
    assert item['unit_cost'] == 4.0, (
        f"a later products.avg_cost/cost_price edit moved a PAST sale's "
        f"reported cost basis to {item['unit_cost']} -- the exact "
        f"retroactive-profit defect schema v39 exists to kill")


# ── 5. The sale's own COGS journal, and the trial balance still ties ───────

def test_sale_posts_cogs_for_exactly_sum_quantity_times_unit_cost(client):
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=10.0, tax_rate=10)
    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0, amount_paid=40.0)

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 3}],
        'payment_method': 'cash', 'amount_paid': 33.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']

    lines = _cogs_lines(cid, 'sale', sale_id)
    by_code = {l['account_code']: l for l in lines}
    assert len(lines) == 2, lines
    # 3 units * 4.00 avg_cost = 12.00, Dr COGS / Cr Inventory.
    assert by_code[gl.ACCOUNT_COGS]['debit'] == 12.0
    assert by_code[gl.ACCOUNT_COGS]['credit'] == 0.0
    assert by_code[gl.ACCOUNT_INVENTORY]['credit'] == 12.0
    assert by_code[gl.ACCOUNT_INVENTORY]['debit'] == 0.0

    tb = _trial_balance(cid)
    assert tb['is_balanced']
    assert tb['total_debit'] == tb['total_credit']


# ── 6. A return posts the mirror, sourced from the ORIGINAL snapshot ───────

def test_return_posts_the_cogs_mirror_and_ties_out(client):
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=10.0, tax_rate=0)
    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0, amount_paid=40.0)

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}],
        'payment_method': 'cash', 'amount_paid': 20.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']

    r = client.get(f'{API}/sales/{sale["id"]}')
    line = r.get_json()['data']['items'][0]
    r = client.post(f'{API}/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': pid, 'quantity': 2, 'sale_item_id': line['id']}],
        'reason': 'cogs mirror test', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    ret_id = r.get_json()['data']['id']

    lines = _cogs_lines(cid, 'return', ret_id)
    by_code = {l['account_code']: l for l in lines}
    assert len(lines) == 2, lines
    # Full return of 2 units @ the ORIGINAL 4.00 snapshot = 8.00, mirrored:
    # Cr COGS / Dr Inventory.
    assert by_code[gl.ACCOUNT_COGS]['credit'] == 8.0
    assert by_code[gl.ACCOUNT_COGS]['debit'] == 0.0
    assert by_code[gl.ACCOUNT_INVENTORY]['debit'] == 8.0
    assert by_code[gl.ACCOUNT_INVENTORY]['credit'] == 0.0

    tb = _trial_balance(cid)
    assert tb['is_balanced']


def test_partial_return_posts_proportional_cogs_reversal_not_the_whole_line(client):
    """ADVERSARIAL FINDING, PROVEN RATHER THAN MERELY READ (task's own
    instruction: "does a partial return post a proportional COGS
    reversal, or the whole line's cost?"). retail_api.py's `cogs_reversal_
    amount` sums `item['quantity']` -- the RETURN's OWN requested quantity,
    never the original sale line's full quantity -- times `unit_cost_
    snapshot` (the original line's cost). A return for 1 of 4 units must
    therefore reverse 1*unit_cost, not the whole line's 4*unit_cost."""
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=10.0, tax_rate=0)
    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0, amount_paid=40.0)

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 4}],
        'payment_method': 'cash', 'amount_paid': 40.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']
    sale_lines = _cogs_lines(cid, 'sale', sale['id'])
    assert {l['account_code']: l for l in sale_lines}[gl.ACCOUNT_COGS]['debit'] == 16.0  # 4*4.00

    r = client.get(f'{API}/sales/{sale["id"]}')
    line = r.get_json()['data']['items'][0]
    r = client.post(f'{API}/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': pid, 'quantity': 1, 'sale_item_id': line['id']}],  # only 1 of 4
        'reason': 'partial return proportional cogs', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    ret_id = r.get_json()['data']['id']

    ret_lines = _cogs_lines(cid, 'return', ret_id)
    by_code = {l['account_code']: l for l in ret_lines}
    assert by_code[gl.ACCOUNT_COGS]['credit'] == 4.0, (
        f"a partial return of 1-of-4 units must reverse 1*unit_cost=4.00, "
        f"not the whole line's 16.00 -- got "
        f"{by_code[gl.ACCOUNT_COGS]['credit']}")
    assert by_code[gl.ACCOUNT_INVENTORY]['debit'] == 4.0

    tb = _trial_balance(cid)
    assert tb['is_balanced']


# ── 7. No cost basis at all: no COGS journal, sale still succeeds ──────────

def test_sale_with_unknown_cost_basis_posts_no_cogs_journal_but_still_succeeds(client):
    """`avg_cost` AND `cost_price` both 0 -- never a PO receipt, never a
    typed cost -- is "no cost basis exists anywhere for this line", which
    post_cogs's own epsilon gate treats as silence, never as a fabricated
    zero-value journal AND never as a blocked sale (post_cogs's return
    value is never treated as pass/fail by its callers -- see that
    function's own docstring)."""
    cid = client.test_company_id
    # `initial_stock=1` -- this test's claim is about COST BASIS, not stock
    # availability; the product must be sellable at all despite carrying no
    # cost basis, or create_sale's own insufficient-stock refusal (unrelated
    # to this test) masks the assertion this test actually exists to make.
    pid = _seed_product(client, sell_price=10.0, tax_rate=0, cost_price=0, initial_stock=1)
    cost = _product_cost(cid, pid)
    assert not cost['avg_cost']
    assert not cost['cost_price']

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 10.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']

    assert _cogs_lines(cid, 'sale', sale_id) == [], (
        'a sale with no known cost basis anywhere must post NO COGS '
        'journal at all, not a fabricated zero-value one')

    tb = _trial_balance(cid)
    assert tb['is_balanced']


# ── 8. Returning a pre-v39 line: no fabricated reversal, no crash ──────────

def test_returning_a_pre_v39_sale_line_posts_no_cogs_reversal_and_does_not_raise(client):
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=10.0, tax_rate=0)
    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0, amount_paid=40.0)

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 10.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']

    items = _sale_item_rows(cid, sale['id'])
    assert items[0]['unit_cost'] == 4.0  # this line DOES have a real snapshot...
    _force_sale_item_unit_cost(items[0]['id'], 0)  # ...forced back to the pre-v39 sentinel

    r = client.get(f'{API}/sales/{sale["id"]}')
    line = r.get_json()['data']['items'][0]
    assert not line['unit_cost']

    r = client.post(f'{API}/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': pid, 'quantity': 1, 'sale_item_id': line['id']}],
        'reason': 'pre-v39 line return', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    ret_id = r.get_json()['data']['id']

    assert _cogs_lines(cid, 'return', ret_id) == [], (
        'a return of a line with no known cost snapshot must post NO COGS '
        'reversal -- 0 here means "unknown", never "free goods"')

    tb = _trial_balance(cid)
    assert tb['is_balanced']


# ── 9. Sync payload coverage -- functional, not merely key-presence ────────

def test_sale_item_unit_cost_appears_in_its_sync_payload(client):
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=10.0, tax_rate=0)
    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0)

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 10.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']
    item_uid = _sale_item_rows(cid, sale_id)[0]['uid']

    payload = _sync_payload('sale_item', 'create', item_uid)
    assert payload is not None, 'no sale_item sync_outbox row was queued at all'
    assert payload['unit_cost'] == 4.0, (
        f"queued sale_item payload's unit_cost is {payload.get('unit_cost')!r}, "
        f"not the real 4.00 snapshot -- a peer device pulling this row via "
        f"sync would show this sale's cost as whatever ITS OWN local "
        f"avg_cost happens to be right now")


def test_product_avg_cost_appears_in_its_update_sync_payload(client):
    """`product`/`create` always hardcodes `avg_cost: 0` (retail_api.py's
    own comment: a brand-new product has never had a receipt), so the ONLY
    site that can ever carry a REAL, nonzero avg_cost on the wire is the
    `product`/`update` event a PATCH queues from a fresh row re-read.
    Proven functionally: PATCH some unrelated field (`name`) after a real
    receipt has set a real average, and check the number, not merely that
    the key exists."""
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client)
    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0)
    assert _product_cost(cid, pid)['avg_cost'] == 4.0

    r = client.patch(f'{API}/products/{pid}', json={'name': 'Renamed for sync coverage'})
    assert r.status_code == 200, r.get_json()

    payload = _sync_payload('product', 'update', pid)
    assert payload is not None, 'no product update sync_outbox row was queued at all'
    assert payload['avg_cost'] == 4.0, (
        f"queued product update payload's avg_cost is "
        f"{payload.get('avg_cost')!r}, not the real 4.00 average -- a "
        f"device that first learns about this product through this PATCH "
        f"(rather than through its own create/sync of the original row) "
        f"would silently zero out its cost basis")


def test_points_redeemed_amount_appears_in_the_sale_sync_payload(client):
    """`sale`/`create` used to omit `points_redeemed_amount` entirely (a
    real, pre-existing bug fixed in the SAME pass as v39 -- see this
    file's module docstring / retail_api.py's own comment at the queue
    site), so a peer device showed every redeemed sale as having redeemed
    0 points forever. Proven functionally: ring a sale that actually
    redeems points and assert the queued payload carries the REAL
    redeemed amount -- a payload that merely carries the KEY, hardcoding
    0, would pass a presence-only check but fail this one."""
    cid = client.test_company_id
    cust_id = _seed_customer(client)
    _set_loyalty_point_value(client, 1)  # 1 point = 1 currency unit
    _grant_ledger_points(cid, cust_id, 20)
    # `initial_stock=1` -- this test's claim is about the SYNC PAYLOAD, not
    # the cost basis, so the product just needs to be sellable at all (this
    # file's own `_seed_product` default of 0 exists for the weighted-
    # average tests above, not for this one).
    pid = _seed_product(client, sell_price=100.0, tax_rate=0, initial_stock=1)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'customer_id': cust_id,
        'points_redeemed': 15, 'amount_paid': 100.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']
    assert body['points_redeemed_amount'] == 15.0
    sale_uid = _sale_uid(cid, body['id'])

    payload = _sync_payload('sale', 'create', sale_uid)
    assert payload is not None, 'no sale sync_outbox row was queued at all'
    assert payload['points_redeemed_amount'] == 15.0, (
        f"queued sale payload's points_redeemed_amount is "
        f"{payload.get('points_redeemed_amount')!r}, not the real 15.0 -- "
        f"a peer device would show this sale as having redeemed 0 points, "
        f"forever, with no later event that could ever correct it")


# ── 10. DEFECT 1 -- a negative moving average must never be persisted ──────
#
# The `denom<=0` guard already in receive_purchase_order only catches a
# zero/negative DENOMINATOR. It says nothing about a negative NUMERATOR
# reaching a POSITIVE denominator -- and `qty_on_hand` (summed company-wide,
# see that route's own comment) can legitimately be negative through create_
# sale's `behind_on_sync` oversell path, without this receipt having done
# anything wrong. Reproduced with the task's own numbers below: old_avg=10,
# qty_on_hand=-5, receive 6 @ 5.00 -- the raw formula gives
# (-5*10 + 6*5) / (-5+6) = -20.00 / 1 = -20.00.

def test_receipt_that_would_go_negative_falls_back_to_received_cost(client):
    """The fix: fall back to the RECEIVED unit cost (an OBSERVED fact of
    THIS transaction), never persist the negative figure and never clamp
    to 0 -- this codebase's cost model reads 0 as its own sentinel for "no
    cost basis known at all" (metrics.py falls back to cost_price when
    avg_cost is 0; post_cogs posts nothing for a 0 total), so clamping
    would misreport a product that DOES have a known cost as one that has
    none."""
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=50.0, tax_rate=0)
    _force_product_cost(cid, pid, avg_cost=10.0)
    _force_negative_stock(cid, pid, -5)

    _po_receipt(client, supp_id, pid, qty=6, unit_cost=5.0)

    assert _product_cost(cid, pid)['avg_cost'] == 5.0, (
        "a receipt whose formula would go negative must fall back to the "
        "RECEIVED unit cost (5.00), not persist a negative average or "
        "clamp to 0")


def test_receipt_that_would_go_negative_never_reaches_a_sale_snapshot_negative(client):
    """The positive fallback must also be what a LATER sale snapshots --
    proving the fix all the way through to `sale_items.unit_cost`, not
    merely to `products.avg_cost`, the same "prove it end to end" posture
    test 3/4 above already take for the ordinary weighted-average path."""
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=50.0, tax_rate=0)
    _force_product_cost(cid, pid, avg_cost=10.0)
    _force_negative_stock(cid, pid, -5)
    _po_receipt(client, supp_id, pid, qty=6, unit_cost=5.0)  # balance now -5+6=1

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 50.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']

    items = _sale_item_rows(cid, sale_id)
    assert items[0]['unit_cost'] == 5.0, (
        f"a sale rung against a product whose last receipt would have "
        f"gone negative snapshotted {items[0]['unit_cost']!r}, not the "
        f"positive received-cost fallback")

    counts = _negative_cost_counts(cid)
    assert counts == {'avg_cost': 0, 'unit_cost': 0}, (
        f"a negative cost figure survived somewhere in this company's own "
        f"data: {counts}")


def test_denom_leq_zero_still_keeps_the_old_average_unchanged(client):
    """Regression guard for the PRE-EXISTING `denom<=0` branch, untouched
    by this fix: `qty_on_hand=-10`, receive 5 units -> denom = -10+5 = -5
    <= 0, which must keep the OLD average exactly as it was, not adopt
    the received cost (this test's own 99.0, chosen to be unmistakably
    different from either the old average or a plausible accident) or any
    other figure."""
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client)
    _force_product_cost(cid, pid, avg_cost=7.0)
    _force_negative_stock(cid, pid, -10)

    _po_receipt(client, supp_id, pid, qty=5, unit_cost=99.0)

    assert _product_cost(cid, pid)['avg_cost'] == 7.0, (
        "denom<=0 must keep the OLD average unchanged -- the pre-existing "
        "guard this fix must not regress")


# ── 11. DEFECT 2 -- a PO receipt must queue its own product sync event ─────
#
# receive_purchase_order used to update products.avg_cost with NO matching
# sync event at all: the sync_outbox row count for entity_type='product' was
# measured unchanged before and after a receipt. A peer device therefore
# never learned the new average; only a LATER, unrelated PATCH to that same
# product (update_product's own re-read-and-requeue) would ever carry the
# real figure across the wire.

def test_po_receipt_queues_a_product_update_event_with_the_new_avg_cost(client):
    """Proven functionally, matching test_product_avg_cost_appears_in_its_
    update_sync_payload's own style in section 9 above: check the actual
    queued NUMBER against what is really in the database, not merely that
    a sync_outbox row exists at all."""
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client)

    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0)

    assert _product_cost(cid, pid)['avg_cost'] == 4.0
    payload = _sync_payload('product', 'update', pid)
    assert payload is not None, (
        'a PO receipt that set a real avg_cost queued no product sync '
        'event at all -- a peer device will never learn the new average')
    assert payload['avg_cost'] == 4.0, (
        f"queued product update payload's avg_cost is "
        f"{payload.get('avg_cost')!r}, not the real 4.00 average this "
        f"receipt just set in the database")
    assert payload['_changed_fields'] == ['avg_cost'], (
        f"a receipt only ever changes avg_cost on products -- "
        f"_changed_fields was {payload.get('_changed_fields')!r}")


def test_po_receipt_that_does_not_move_avg_cost_emits_no_spurious_event(client):
    """A second receipt landing at EXACTLY the prior average (a second
    delivery at an identical price -- (10*4.0 + 10*4.0)/20 = 4.0,
    unchanged) is not new information for any peer and must not queue a
    redundant product update event. Proven with a before/after COUNT,
    isolated to this product's own id, never a bare presence check --
    see `_product_update_event_count`'s own docstring."""
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client)
    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0)
    assert _product_cost(cid, pid)['avg_cost'] == 4.0
    before = _product_update_event_count(pid)
    assert before == 1  # the real event the first receipt above just queued

    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0)  # avg_cost stays 4.0

    assert _product_cost(cid, pid)['avg_cost'] == 4.0
    after = _product_update_event_count(pid)
    assert after == before, (
        f"a receipt that does not move avg_cost queued a spurious product "
        f"update event ({after - before} extra) -- a peer gains nothing "
        f"from re-sending a value it already has")
