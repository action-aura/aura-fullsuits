"""
Aura Retail -- schema v40 (per-branch cost basis, and a transfer that
carries cost) HTTP-driven proof.

ROADMAP.md's 2026-09-22 "retail schema v40 CLAIMED: a per-branch cost
basis" entry is the claim this file proves was cashed correctly. v39 gave
`products.avg_cost`, a single COMPANY-scoped moving weighted average, and
left inter-branch transfers writing `unit_cost` 0 into `inventory_
movements` -- deliberately harmless while cost was company-wide (moving
stock inside a company does not change the company's total cost). v40
makes cost PER-BRANCH (`inventory_balances.avg_cost`, keyed by the same
`UNIQUE(company_id,product_id,branch_id)` that table already declares) and
makes that same zero UNSAFE: stock leaving a branch that paid 4.00 and
landing at one that paid 10.00 must carry its 4.00 with it, or the
receiver values it at nothing and the sender keeps value it no longer
holds.

Real Flask routes throughout -- receive_purchase_order, send_stock_
transfer, receive_stock_transfer, create_sale -- never a hand-built
journal or a shortcut around the routes; `inventory_balances`/
`stock_transfer_items`/`sale_items`/`products` are read back directly only
to VERIFY what the routes claim to have done, matching retail_stock_
transfer_test.py's own stated posture and retail_gl_cogs_test.py's own
bootstrap shape (no shared conftest.py exists for products/retail/tests/,
so this file is self-contained like both).

CRITICAL: exactly ONE pytest process per file (AUDIT-010) -- these test
files pollute each other in a shared pytest process; see products/
run_all_tests.py.

Run:
    pytest products/retail/tests/retail_v40_branch_cost_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_v40_branchcost_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from core.retail import metrics  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Shop bootstrap -- same shape as retail_stock_transfer_test.py's own
#    _make_user/_make_shop (admin role, explicitly seeded capabilities --
#    stock-transfer routes are gated CAP_STOCK_ADJUST, and this file needs
#    that gate open exactly the same way that file does) ──────────────────

def _make_user(role, company_id, *, email_prefix='v40cost'):
    email = f"{email_prefix}-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "V40BranchCostPW1"  # pragma: allowlist secret -- throwaway fixture value
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
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


def _make_shop_with_two_branches():
    """One company, an admin, TWO branches -- no stock seeded anywhere; each
    test gives its own product(s) stock through real PO receipts, exactly
    like retail_gl_cogs_test.py's own `_seed_product(initial_stock=0)`
    convention (that file's own comment explains why: a nonzero opening
    balance a test did not put there would silently change every
    hand-computed weighted-average figure below it)."""
    company_id = str(uuid.uuid4())
    admin = _make_user('admin', company_id)
    admin.get(f'{API}/settings/tax')  # warms _ensure_credit_schema / doc_sequences

    # try/finally throughout: a leaked, uncommitted connection from an
    # earlier failing test has already been observed to lock EVERY later
    # test in this file out of the one physical sqlite file they all
    # share -- see _seed_balance_qty's own docstring for the fix applied
    # there after that was caught here first.
    dconn = get_retail_conn()
    try:
        dconn.execute(
            "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
            (company_id, uuid.uuid4().int % 4_000_000 + 1),
        )
        dconn.execute("INSERT INTO branches (company_id,name) VALUES (?,'Branch A')", (company_id,))
        dconn.execute("INSERT INTO branches (company_id,name) VALUES (?,'Branch B')", (company_id,))
        branch_rows = dconn.execute(
            "SELECT id FROM branches WHERE company_id=? ORDER BY id", (company_id,)
        ).fetchall()
        bid_a, bid_b = branch_rows[0]['id'], branch_rows[1]['id']
        dconn.commit()
    finally:
        dconn.close()
    return admin, company_id, bid_a, bid_b


# ── Seed / action helpers ────────────────────────────────────────────────

def _seed_product(c, **kw):
    payload = {'name': 'Branch Cost Widget', 'sku': f'V40BC-{uuid.uuid4().hex[:8]}',
               'cost_price': 1.0, 'sell_price': 50.0, 'tax_rate': 0, 'initial_stock': 0}
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_supplier(c):
    r = c.post(f'{API}/suppliers', json={'name': f'V40 Supplier {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _po_receipt(c, supplier_id, pid, branch_id, qty, unit_cost):
    """Create + receive in one call, into a SPECIFIC branch -- `products.
    avg_cost`/`inventory_balances.avg_cost` are maintained ONLY by receive_
    purchase_order (see that route's own moving-average comment), and
    create_purchase_order alone never moves either, so every test that
    needs a real branch-level cost basis goes through both steps against an
    explicit `branch_id`, never the pinned/default branch."""
    r = c.post(f'{API}/purchase-orders', json={
        'supplier_id': supplier_id, 'branch_id': branch_id,
        'items': [{'product_id': pid, 'quantity': qty, 'unit_cost': unit_cost}]})
    assert r.status_code == 200, r.get_json()
    po_id = r.get_json()['data']['id']
    r = c.post(f'{API}/purchase-orders/{po_id}/receive')
    assert r.status_code == 200, r.get_json()
    return po_id


def _sell(c, pid, branch_id, qty):
    r = c.post(f'{API}/sales', json={
        'branch_id': branch_id,
        'items': [{'product_id': pid, 'quantity': qty}],
        'payment_method': 'cash', 'amount_paid': 1_000_000.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_transfer(c, source_bid, dest_bid, pid, qty):
    r = c.post(f'{API}/stock-transfers', json={
        'source_branch_id': source_bid, 'destination_branch_id': dest_bid,
        'items': [{'product_id': pid, 'quantity': qty}]})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _send_transfer(c, transfer_id):
    r = c.post(f'{API}/stock-transfers/{transfer_id}/send')
    assert r.status_code == 200, r.get_json()


def _receive_transfer(c, transfer_id, item_id, qty_received):
    r = c.post(f'{API}/stock-transfers/{transfer_id}/receive', json={
        'items': [{'id': item_id, 'quantity_received': qty_received}]})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _first_item_id(c, transfer_id):
    r = c.get(f'{API}/stock-transfers/{transfer_id}')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['items'][0]['id']


# ── Read helpers (raw SQL, same posture as retail_gl_cogs_test.py's own
#    `_product_cost`/`_sale_item_rows`: reading state a route does not
#    itself expose through a JSON response) ────────────────────────────────

def _branch_avg_cost(cid, pid, bid):
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT avg_cost FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
            (cid, pid, bid)
        ).fetchone()
        return float(row['avg_cost']) if row and row['avg_cost'] is not None else 0.0
    finally:
        conn.close()


def _branch_qty(cid, pid, bid):
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
            (cid, pid, bid)
        ).fetchone()
        return float(row['quantity_on_hand']) if row else 0.0
    finally:
        conn.close()


def _product_avg_cost(cid, pid):
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT avg_cost FROM products WHERE id=? AND company_id=?", (pid, cid)
        ).fetchone()
        return float(row['avg_cost']) if row else 0.0
    finally:
        conn.close()


def _sale_item_unit_cost(sale_id):
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT unit_cost FROM sale_items WHERE sale_id=? ORDER BY id LIMIT 1", (sale_id,)
        ).fetchone()
        return float(row['unit_cost']) if row else None
    finally:
        conn.close()


def _movement_unit_cost(cid, pid, bid, movement_type, reference):
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT unit_cost FROM inventory_movements "
            "WHERE company_id=? AND product_id=? AND branch_id=? AND movement_type=? AND reference=? "
            "ORDER BY id DESC LIMIT 1",
            (cid, pid, bid, movement_type, reference)
        ).fetchone()
        return float(row['unit_cost']) if row else None
    finally:
        conn.close()


def _transfer_item_unit_cost(item_id):
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT unit_cost FROM stock_transfer_items WHERE id=?", (item_id,)
        ).fetchone()
        return float(row['unit_cost']) if row else None
    finally:
        conn.close()


def _cogs_lines_exist(cid, source_type, source_id):
    """Whether post_cogs posted ANYTHING for this document -- see retail_gl_
    cogs_test.py's own `_cogs_lines` docstring: empty means the epsilon-gated
    "no known cost basis anywhere" case, never a partial line that merely
    rounds small."""
    import core.retail.general_ledger as gl
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT 1 FROM gl_journals j JOIN gl_journal_lines l ON l.journal_id = j.id "
            "JOIN gl_accounts a ON a.id = l.account_id "
            "WHERE j.company_id=? AND j.source_type=? AND j.source_id=? AND a.code IN (?,?)",
            (cid, source_type, source_id, gl.ACCOUNT_COGS, gl.ACCOUNT_INVENTORY)
        ).fetchall()
        return len(rows) > 0
    finally:
        conn.close()


def _seed_balance_qty(cid, pid, bid, quantity_on_hand):
    """Test-only direct write: gives a branch on-hand stock with NO
    branch-level average of its own (avg_cost left at the column default,
    0) -- the shape an opening-balance import leaves behind (see database/
    schema.py's v40 migration docstring, "import_api.py ... never writes
    inventory_balances.avg_cost"). UPSERT, not a plain INSERT: `_seed_
    product`'s own `initial_stock` (even at 0) already seeds a zero row at
    this device's resolved default branch (create_product's own `INSERT
    INTO inventory_balances` runs unconditionally -- see that route's own
    comment), so a bare INSERT against that SAME branch would collide on
    `UNIQUE(company_id,product_id,branch_id)`. try/finally so a failed
    assertion elsewhere in a test can never leak this connection and lock
    the next test out of the one physical sqlite file every test in this
    module shares."""
    conn = get_retail_conn()
    try:
        conn.execute(
            "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(company_id,product_id,branch_id) DO UPDATE SET "
            "quantity_on_hand=excluded.quantity_on_hand",
            (cid, pid, bid, quantity_on_hand)
        )
        conn.commit()
    finally:
        conn.close()


def _set_negative_branch_balance(cid, pid, bid, quantity_on_hand, avg_cost):
    """Test-only direct write, same posture as retail_gl_cogs_test.py's own
    `_force_product_cost`: simulates the state the `behind_on_sync` oversell
    path leaves behind (a branch balance driven negative by a sale a device
    accepted past its own recorded stock) WITHOUT needing to reproduce that
    entire multi-device flow through the real routes just to get one branch
    into a negative-on-hand state. See create_sale's own `behind_on_sync`
    comment block for how this state arises for real. UPSERT + try/finally,
    same reasoning as `_seed_balance_qty` immediately above."""
    conn = get_retail_conn()
    try:
        conn.execute(
            "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand,avg_cost) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(company_id,product_id,branch_id) DO UPDATE SET "
            "quantity_on_hand=excluded.quantity_on_hand, avg_cost=excluded.avg_cost",
            (cid, pid, bid, quantity_on_hand, avg_cost)
        )
        conn.commit()
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 1. Headline property: a receipt into branch A sets A's average and
#    leaves branch B's untouched.
# ═════════════════════════════════════════════════════════════════════════

def test_receipt_into_branch_a_leaves_branch_b_untouched():
    admin, cid, bid_a, bid_b = _make_shop_with_two_branches()
    supplier_id = _seed_supplier(admin)
    pid = _seed_product(admin, cost_price=1.0)

    _po_receipt(admin, supplier_id, pid, bid_a, qty=10, unit_cost=4.00)

    assert _branch_avg_cost(cid, pid, bid_a) == 4.00
    assert _branch_avg_cost(cid, pid, bid_b) == 0.0, \
        "branch B never received stock -- its own avg_cost must still be the column default"
    # Company-wide products.avg_cost is unchanged by v40 -- still maintained
    # by every receipt regardless of branch, per ROADMAP.md's v40 entry
    # ("products.avg_cost STAYS as the company-level fallback").
    assert _product_avg_cost(cid, pid) == 4.00


# ═════════════════════════════════════════════════════════════════════════
# 2. Two receipts at different prices into the SAME branch -- exact
#    weighted average, hand-computed.
# ═════════════════════════════════════════════════════════════════════════

def test_two_receipts_same_branch_exact_weighted_average():
    admin, cid, bid_a, bid_b = _make_shop_with_two_branches()
    supplier_id = _seed_supplier(admin)
    pid = _seed_product(admin, cost_price=1.0)

    _po_receipt(admin, supplier_id, pid, bid_a, qty=10, unit_cost=4.00)
    assert _branch_avg_cost(cid, pid, bid_a) == 4.00

    _po_receipt(admin, supplier_id, pid, bid_a, qty=5, unit_cost=10.00)
    # (10*4.00 + 5*10.00) / 15 = (40 + 50) / 15 = 90 / 15 = 6.00 exactly --
    # "it moved" would pass for a wrong formula (e.g. a plain (4+10)/2=7.00
    # unweighted average); this pins the EXACT figure.
    assert _branch_avg_cost(cid, pid, bid_a) == 6.00
    assert _branch_qty(cid, pid, bid_a) == 15.0


# ═════════════════════════════════════════════════════════════════════════
# 3. A sale in branch A snapshots A's cost; the SAME product sold in
#    branch B snapshots B's -- different figures, same SKU, same day.
# ═════════════════════════════════════════════════════════════════════════

def test_sale_snapshots_the_selling_branchs_own_cost():
    admin, cid, bid_a, bid_b = _make_shop_with_two_branches()
    supplier_id = _seed_supplier(admin)
    pid = _seed_product(admin, cost_price=1.0)

    _po_receipt(admin, supplier_id, pid, bid_a, qty=10, unit_cost=4.00)
    _po_receipt(admin, supplier_id, pid, bid_b, qty=10, unit_cost=10.00)

    sale_a = _sell(admin, pid, bid_a, qty=1)
    sale_b = _sell(admin, pid, bid_b, qty=1)

    assert _sale_item_unit_cost(sale_a) == 4.00
    assert _sale_item_unit_cost(sale_b) == 10.00, \
        "the whole reason v40 exists: the same SKU sold from two branches " \
        "that paid different prices must snapshot two different costs"


# ═════════════════════════════════════════════════════════════════════════
# 4. A transfer carries cost: A -> B, and B's average afterwards is the
#    correct weighted blend of what B already held and what arrived.
# ═════════════════════════════════════════════════════════════════════════

def test_transfer_carries_cost_and_blends_into_destination_average():
    admin, cid, bid_a, bid_b = _make_shop_with_two_branches()
    supplier_id = _seed_supplier(admin)
    pid = _seed_product(admin, cost_price=1.0)

    _po_receipt(admin, supplier_id, pid, bid_a, qty=20, unit_cost=4.00)   # A's avg = 4.00
    _po_receipt(admin, supplier_id, pid, bid_b, qty=10, unit_cost=10.00)  # B's avg = 10.00

    transfer_id = _create_transfer(admin, bid_a, bid_b, pid, qty=10)
    _send_transfer(admin, transfer_id)
    item_id = _first_item_id(admin, transfer_id)
    reference = f'XFER-{transfer_id}'

    # SEND: the sending branch's cost is snapshotted onto the movement AND
    # the transfer line -- the movement no longer records the old, always-0
    # unit_cost.
    assert _transfer_item_unit_cost(item_id) == 4.00
    assert _movement_unit_cost(cid, pid, bid_a, 'transfer_out', reference) == 4.00, \
        "the sending branch's movement must record a NONZERO unit_cost"
    # Sending never changes the SENDER's own average -- removing stock at
    # the existing average does not move a weighted average.
    assert _branch_avg_cost(cid, pid, bid_a) == 4.00

    _receive_transfer(admin, transfer_id, item_id, qty_received=10)

    # RECEIVE: the transfer_in movement carries the SAME sender's cost
    # forward (never recomputed at the destination).
    assert _movement_unit_cost(cid, pid, bid_b, 'transfer_in', reference) == 4.00
    # B held 10 @ 10.00 and received 10 @ 4.00:
    # (10*10.00 + 10*4.00) / 20 = (100 + 40) / 20 = 140 / 20 = 7.00 exactly.
    assert _branch_avg_cost(cid, pid, bid_b) == 7.00
    assert _branch_qty(cid, pid, bid_b) == 20.0
    # A's own average is still untouched after the full round trip.
    assert _branch_avg_cost(cid, pid, bid_a) == 4.00
    assert _branch_qty(cid, pid, bid_a) == 10.0


# ═════════════════════════════════════════════════════════════════════════
# 5. Transferring into an EMPTY branch gives it exactly the sender's cost.
# ═════════════════════════════════════════════════════════════════════════

def test_transfer_into_empty_branch_gives_exactly_senders_cost():
    admin, cid, bid_a, bid_b = _make_shop_with_two_branches()
    supplier_id = _seed_supplier(admin)
    pid = _seed_product(admin, cost_price=1.0)

    _po_receipt(admin, supplier_id, pid, bid_a, qty=5, unit_cost=8.00)
    assert _branch_qty(cid, pid, bid_b) == 0.0, "branch B must start genuinely empty"

    transfer_id = _create_transfer(admin, bid_a, bid_b, pid, qty=5)
    _send_transfer(admin, transfer_id)
    item_id = _first_item_id(admin, transfer_id)
    _receive_transfer(admin, transfer_id, item_id, qty_received=5)

    # denom = 0 (on hand) + 5 (arriving) -> formula collapses to exactly
    # the incoming cost, no special-casing needed, same as a first PO
    # receipt into an empty branch.
    assert _branch_avg_cost(cid, pid, bid_b) == 8.00
    assert _branch_qty(cid, pid, bid_b) == 5.0


# ═════════════════════════════════════════════════════════════════════════
# 6. The fallback chain: branch avg_cost -> products.avg_cost ->
#    cost_price -> no COGS at all when nothing is known anywhere.
# ═════════════════════════════════════════════════════════════════════════

def test_fallback_falls_through_to_company_avg_when_branch_has_none():
    """Branch A receives stock (setting BOTH branch A's avg_cost AND the
    company-wide products.avg_cost, since it is this product's first-ever
    receipt anywhere). Branch B then gets on-hand stock WITHOUT a
    branch-level average of its own (simulating an opening balance import
    -- see database/schema.py's v40 migration docstring, "import_api.py's
    bulk-import stock-declaration path never writes inventory_balances.
    avg_cost"). A sale from B must fall through to the company-wide
    figure, not to a false zero."""
    admin, cid, bid_a, bid_b = _make_shop_with_two_branches()
    supplier_id = _seed_supplier(admin)
    pid = _seed_product(admin, cost_price=1.0)

    _po_receipt(admin, supplier_id, pid, bid_a, qty=10, unit_cost=4.00)
    assert _product_avg_cost(cid, pid) == 4.00

    # Branch B gets on-hand stock directly (no receipt, no branch avg_cost
    # of its own) -- the import-path shape this fallback exists to cover.
    _seed_balance_qty(cid, pid, bid_b, 3)
    assert _branch_avg_cost(cid, pid, bid_b) == 0.0

    sale_b = _sell(admin, pid, bid_b, qty=1)
    assert _sale_item_unit_cost(sale_b) == 4.00, \
        "branch B has no average of its own -- must fall back to products.avg_cost"


def test_fallback_falls_through_to_cost_price_when_no_avg_cost_anywhere():
    admin, cid, bid_a, bid_b = _make_shop_with_two_branches()
    pid = _seed_product(admin, cost_price=2.50)  # no PO receipt ever happens

    _seed_balance_qty(cid, pid, bid_a, 5)
    assert _branch_avg_cost(cid, pid, bid_a) == 0.0
    assert _product_avg_cost(cid, pid) == 0.0

    sale_id = _sell(admin, pid, bid_a, qty=1)
    assert _sale_item_unit_cost(sale_id) == 2.50


def test_product_with_no_cost_anywhere_posts_no_cogs():
    admin, cid, bid_a, bid_b = _make_shop_with_two_branches()
    pid = _seed_product(admin, cost_price=0.0)  # no cost anywhere at all

    _seed_balance_qty(cid, pid, bid_a, 5)

    sale_id = _sell(admin, pid, bid_a, qty=1)
    assert _sale_item_unit_cost(sale_id) == 0.0
    assert _cogs_lines_exist(cid, 'sale', sale_id) is False, \
        "no known cost basis anywhere -- post_cogs's own epsilon gate must post NOTHING, not a zero line"


# ═════════════════════════════════════════════════════════════════════════
# 7. No per-branch average is ever negative, including on the oversell
#    (behind_on_sync) path.
# ═════════════════════════════════════════════════════════════════════════

def test_branch_average_never_goes_negative_on_a_receipt_after_oversell():
    """Reproduces, per branch, the exact scenario api/retail_api.py's own
    receive_purchase_order AUDIT comment (2026-09-21) documents for the
    company-wide figure: old_avg=10, qty_on_hand=-5 (left behind by the
    behind_on_sync oversell path), receive 6 @ 5.00. Unguarded: ((-5)*10 +
    6*5)/1 = (-50+30)/1 = -20.00. Guarded: falls back to the observed
    receipt price, 5.00 -- not a 0-clamp, matching the company-wide guard's
    own reasoning (0 is this file's OWN sentinel for "no cost basis known
    at all", and clamping to it would misreport a receipt that DOES have a
    known cost as one that has none)."""
    admin, cid, bid_a, bid_b = _make_shop_with_two_branches()
    supplier_id = _seed_supplier(admin)
    pid = _seed_product(admin, cost_price=1.0)

    _set_negative_branch_balance(cid, pid, bid_a, quantity_on_hand=-5, avg_cost=10.0)

    _po_receipt(admin, supplier_id, pid, bid_a, qty=6, unit_cost=5.00)

    new_avg = _branch_avg_cost(cid, pid, bid_a)
    assert new_avg >= 0.0, f"branch avg_cost must never go negative, got {new_avg!r}"
    assert new_avg == 5.00


# ═════════════════════════════════════════════════════════════════════════
# 8. inventory_value() values per branch, and the branches sum to the
#    company total.
# ═════════════════════════════════════════════════════════════════════════

def test_inventory_value_per_branch_sums_to_company_total():
    admin, cid, bid_a, bid_b = _make_shop_with_two_branches()
    supplier_id = _seed_supplier(admin)
    pid = _seed_product(admin, cost_price=1.0)

    _po_receipt(admin, supplier_id, pid, bid_a, qty=20, unit_cost=4.00)   # A: 20 @ 4.00 = 80.00
    _po_receipt(admin, supplier_id, pid, bid_b, qty=10, unit_cost=10.00)  # B: 10 @ 10.00 = 100.00

    conn = get_retail_conn()
    try:
        value_a = metrics.inventory_value(conn, cid, branch_id=bid_a)
        value_b = metrics.inventory_value(conn, cid, branch_id=bid_b)
        value_total = metrics.inventory_value(conn, cid)
    finally:
        conn.close()

    assert value_a == 80.00
    assert value_b == 100.00
    assert value_total == value_a + value_b == 180.00


# ═════════════════════════════════════════════════════════════════════════
# Mutation proofs live in this file's own companion verification pass (see
# the task report) -- each mutation is applied to api/retail_api.py,
# confirmed to turn the named test RED, then restored surgically and
# confirmed GREEN again.
# ═════════════════════════════════════════════════════════════════════════
