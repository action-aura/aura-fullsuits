"""Aura Retail -- JoFotara e-invoicing (docs/einvoicing/phase1/) regression
safety net.

Written BEFORE any e-invoicing feature code exists (Step 3 of this wave's
implementation plan), against the real, currently-shipping backend. Every
frozen literal below (response key lists, column lists) was captured live
from the actual running app, not hand-derived -- see this wave's baseline in
docs/einvoicing/phase1/phase1-scope-and-baseline.md.

The point of this file: as e-invoicing feature code lands in later steps,
these tests keep passing UNCHANGED as long as the feature stays at its
default (OFF). If any of these ever fail after this wave's feature code
lands, that is a real regression against a Wave1C-cleared, pilot-live
product -- not a test to "fix" by updating the frozen literal, unless the
underlying product behavior change was itself deliberate and reviewed.

Bootstrap pattern copied from
products/retail/tests/wave1c_financial_gate_test.py.

Run:
    python -m pytest products/retail/tests/retail_einvoicing_regression_test.py -v
"""
import os
import shutil
import sys
import tempfile
import threading
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_einvoice_regression_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
# AURA_SITE_RELAY_ENABLED="0" is the documented kill switch
# (config.py::site_relay_should_start, tri-state override; "0" wins over
# everything). It is set here because of an interaction that is not obvious:
# this file seeds an ACTIVE licence on platform="WINDOWS" a few lines below,
# since e-invoicing needs one -- and "licensed AND Windows" is EXACTLY the
# condition that makes a till elect itself the LAN site-relay hub. So
# init_app() was starting four real background threads (aura-site-relay,
# -coordinator, -housekeeping, -beacon) and binding a TLS listener, inside a
# regression test that is about e-invoicing worker threads and nothing else.
#
# That is what broke
# test_no_worker_thread_starts_for_a_company_with_no_enqueued_rows_at_boot,
# whose own assertion message says in writing: "do not widen
# BASELINE_THREAD_NAMES". Widening it would have been the wrong fix twice
# over -- it would blind that check to any OTHER unexpected thread (the only
# thing it exists to catch), and it would leave a regression test opening a
# network listener on the machine running it. Turning the relay off keeps the
# baseline strict AND stops the test binding a port.
#
# AURA_BACKUP_SCHEDULER="0" (2026-10-12), same reasoning: BACKUP-1 arms a
# daily backup timer on every boot, which this process-level thread check saw
# as an unexpected 'Thread-1'. Off here, BASELINE_THREAD_NAMES stays strict.
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
                  AURA_SITE_RELAY_ENABLED="0", AURA_BACKUP_SCHEDULER="0")
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from api.retail_api import _ensure_credit_schema  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ─── Frozen literals -- captured live from the real app before any
# e-invoicing code existed. Do not "fix" a failing assertion by editing these
# without confirming the underlying behavior change was deliberate. ─────────
#
# `oversold_past_recorded_stock` added launch-readiness Phase 7 stage
# 7d-iii (docs/launch-readiness/phase7-offline-ux.md "Correction to
# Decision 1") -- confirmed deliberate: create_sale now always reports
# whether a sale was allowed past its recorded on-hand figure. E-invoicing
# still only conditionally adds its own 'einvoice' key; that claim is
# unaffected.

# AUDIT (2026-09-08): e-invoicing used to default ON (settings.py
# DEFAULTS['enabled']='1' -- the Jordanian mandate means a fresh company
# records the obligation without anyone touching a toggle). A sale response
# then carried an additional 'einvoice' key ({'invoice_ref':..., 'status':
# 'queued'}) whenever the enqueue succeeded -- see retail_api.py::create_sale's
# own comment on that block. That was a deliberate, reviewed shape change,
# not a regression to "fix" back to the old list -- at the time.
#
# AUDIT (2026-09-22) -- SUPERSEDES THE PARAGRAPH ABOVE AND REMOVES
# 'einvoice' FROM THIS LIST. e-invoicing is opt-in again (settings.py
# DEFAULTS['tax_regime']='none', checked before `enabled` in is_enabled())
# -- see that key's own comment for the full history: nothing can actually
# be filed yet, so defaulting a fresh company into enqueueing only minted
# unfileable e-invoice numbers. A DEFAULT company's sale response (this
# test's fixture never touches /api/einvoicing/settings) therefore no
# longer carries 'einvoice' at all -- this frozen literal is right to
# change because it is meant to describe the DEFAULT shape, and the default
# shape genuinely changed again. THIS DOES NOT DELETE THE COVERAGE: see
# test_sale_response_gains_einvoice_key_once_opted_into_tax_regime below,
# which proves the key IS present for a company that explicitly opts in --
# the same claim this list used to make for every company, now scoped to
# the case where it's still true.
#   1. Desktop (products/retail/frontend/subsystem-retail.js): _showReceipt/
#      _einvoiceReceiptBlock already access saleData.einvoice by name and
#      explicitly no-op when it's absent -- built for exactly this
#      conditional-key shape, never a strict destructure or key-set check.
#   2. Android (android/aura-retail/.../net/Models.kt's SaleResult data
#      class + ApiClient.kt's Retrofit GsonConverterFactory): Gson silently
#      ignores JSON fields with no matching property -- SaleResult has no
#      `einvoice` field, so it is dropped on parse, not an error. (The
#      "non-lenient GsonConverterFactory throws" comment elsewhere in
#      Models.kt is about TYPE MISMATCHES, e.g. a UUID string into an Int
#      field -- unrelated to an extra, unmapped key.)
#   3. commercial_runtime/sync/: replicates DB ROWS (sync_outbox/sync_cursor
#      against `sales` etc.), never this HTTP response body -- unaffected
#      by any change to what POST /sales returns.
# points_redeemed / points_redeemed_amount joined this set when loyalty
# redemption shipped (v27). They are always present, zero when nothing was
# redeemed, so the receipt and the sale-complete modal can show the SERVER's
# figure rather than re-deriving it client-side. This list stays an EXACT
# match -- widening it does not loosen the check, and a key disappearing or
# an unexpected one appearing still fails.
SALE_RESPONSE_KEYS = [
    'amount_paid', 'balance_due', 'calculation_version', 'change', 'currency',
    'customer_id', 'customer_name', 'discount_amount', 'employee_name',
    'id', 'idempotency_key', 'lines', 'oversold_past_recorded_stock',
    'points_redeemed', 'points_redeemed_amount',
    'sale_number', 'subtotal', 'tax_amount', 'total', 'warning',
]
# The exact same set PLUS 'einvoice' -- see SALE_RESPONSE_KEYS' own comment.
# Kept as its own literal (rather than SALE_RESPONSE_KEYS + ['einvoice'] at
# the call site) so both lists independently show, side by side, exactly
# which one key differs.
SALE_RESPONSE_KEYS_WITH_EINVOICE = sorted(SALE_RESPONSE_KEYS + ['einvoice'])
# The return-settlement split (fix(money) 78469b70) added FOUR keys on
# purpose. This file's docstring allows updating a frozen literal when "the
# underlying product behavior change was itself deliberate and reviewed" --
# it was: create_return now reports how a refund was actually settled, split
# across tender / AR forgiveness / store credit, because reporting only a
# single `refund_amount` is what let a refund be counted as drawer cash AND
# as debt forgiveness at the same time. `refund_amount` itself is UNCHANGED
# in both name and meaning (the tax-inclusive value of the goods returned).
# The set stays exact, so an ACCIDENTAL new key still fails this test.
RETURN_RESPONSE_KEYS = [
    'ar_forgiven_amount', 'calculation_version', 'id', 'idempotency_key',
    'items', 'refund_amount', 'refund_method', 'return_number',
    'store_credit_amount', 'tender_refund_amount',
]
DASHBOARD_DATA_KEYS = [
    'hourly_data', 'hourly_labels', 'low_stock_alerts', 'month_returns',
    'month_sales', 'month_transactions', 'payment_methods', 'recent_sales',
    'sales_change_pct', 'today_returns', 'today_sales', 'today_transactions',
    'total_customers', 'total_products', 'yesterday_sales',
]
SALES_TABLE_COLUMNS = [
    'id', 'company_id', 'sale_number', 'branch_id', 'customer_id', 'cashier',
    'subtotal', 'discount_amount', 'tax_amount', 'total', 'amount_paid',
    'change_amount', 'payment_method', 'status', 'idempotency_key', 'notes',
    'created_at',
    # feat/shift-cash-drawer (schema v10, database/schema.py's
    # _migrate_add_shift_cash_drawer): sales.session_id -- a real, expected
    # additive shape change. It lands BEFORE due_date in this list (not
    # after) because it's added by the versioned schema migration that runs
    # at app boot (init_app() -> init_retail() -> ensure_schema_version()),
    # while due_date is added later, lazily, the first time any request
    # calls _ensure_credit_schema() -- see that function's own addcol('sales',
    # 'due_date', ...) call. SQLite's PRAGMA table_info always reflects
    # physical ALTER TABLE order, not declaration/logical order.
    'session_id',
    # launch-readiness Phase 2 (schema v13, database/schema.py's
    # _migrate_add_identity_and_attribution_columns): the wire identity plus
    # the actor/terminal/UTC-clock triple. Same "real, expected additive
    # shape change" category as session_id above, and it lands in the same
    # position in the list for the same reason -- it is applied by the
    # boot-time versioned migration, so physically before due_date's lazy
    # _ensure_credit_schema addcol. v13 is the first migration to ALTER
    # `sales` at all; the claim this test makes -- that the E-INVOICING
    # migration (v7) added only new tables -- is untouched by it.
    'uid', 'actor_user_uid', 'terminal_id', 'created_at_utc',
    # Loyalty redemption (schema v27, claimed in ROADMAP.md before the work
    # started): sales.points_redeemed_amount, the money value of the points a
    # customer spent on this sale. Same "real, expected additive shape change"
    # category as session_id and the v13 identity triple above, and it sits in
    # the same place for the same reason -- it is applied by the BOOT-TIME
    # versioned migration, so physically before due_date's lazy
    # _ensure_credit_schema addcol, whatever the declaration order suggests.
    # Verified by reading PRAGMA table_info off a freshly migrated database
    # rather than reasoning about it: index 22, with due_date absent until a
    # request triggers the lazy path.
    #
    # The claim this test actually makes -- that the E-INVOICING migration (v7)
    # added only new tables -- is untouched by this, exactly as it was by v10
    # and v13.
    'points_redeemed_amount',
    # W2a (schema v48, database/schema.py's _migrate_add_loyalty_sync):
    # sales.points_earned, how many loyalty points the sale granted, so a
    # computer that receives the sale can tell 'earned nothing' from 'its earn
    # row has not arrived yet'. Same boot-time-migration category and position
    # as points_redeemed_amount above. What this can no longer catch: a stray
    # column NAMED points_earned on sales -- the e-invoicing claim (v7 added
    # only new tables) is unaffected.
    'points_earned',
    'due_date',
]
SALE_ITEMS_TABLE_COLUMNS = [
    'id', 'sale_id', 'product_id', 'quantity', 'unit_price', 'discount_pct',
    'tax_rate', 'line_total',
    # schema v13: sale_items is one of the seven tables given a wire
    # identity, so a relayed line item can be named by something other than
    # this install's private autoincrement id.
    'uid',
    # schema v39 (database/schema.py's _migrate_add_cost_basis, claimed in
    # ROADMAP.md's 2026-09-21 entry): a per-line cost snapshot for Cost of
    # Goods Sold, deliberately left at its DEFAULT 0/unbackfilled for every
    # historical row (see that migration's own docstring for why -- a
    # historical sale's true cost at the moment it happened is unknowable
    # from here). Applied by the BOOT-TIME versioned migration chain (same
    # category as session_id/the v13 identity triple/points_redeemed_amount
    # above on SALES_TABLE_COLUMNS), not a lazy per-request addcol, so this
    # guard still means something: the claim this test makes -- that the
    # E-INVOICING migration (v7) added only new tables -- is unaffected,
    # this column just did not exist yet the last time this literal was
    # captured live.
    'unit_cost',
]
CUSTOMERS_TABLE_COLUMNS = [
    # 'status' and the created_at reorder are a real, expected shape change
    # from the multi-device sync foundation's customers.id -> UUID migration
    # (database/schema.py::_migrate_customers_to_uuid folds in a new status
    # column customers never had, plus the credit_mode/credit_limit/
    # credit_balance columns _ensure_credit_schema previously added at
    # runtime, as first-class columns on the rebuilt table) -- merged in via
    # feat/retail-mobile-build-baseline, 2026-08-10, unrelated to e-invoicing.
    'id', 'company_id', 'name', 'phone', 'email', 'address', 'loyalty_points',
    'total_spent', 'status', 'credit_mode', 'credit_limit', 'credit_balance',
    'created_at',
    # launch-readiness Phase 2 (schema v13): customers is one of the four
    # catalogue tables given the reject-stale marker and soft tombstone --
    # the same triple registry v3 put on `users`. No uid here: catalogue
    # tables already carry a client-generated UUID primary key (see
    # _migrate_customers_to_uuid), so their `id` IS already wire-safe.
    'row_version', 'updated_at_utc', 'deleted_at_utc',
    # schema v49 (B6b): history a customer brought from a previous system. A real,
    # reviewed column unrelated to e-invoicing; appended by the v49 migration step, so
    # it comes last. This pin still catches any OTHER customers column drifting in.
    'total_spent_baseline',
]
RETURNS_TABLE_COLUMNS = [
    'id', 'company_id', 'return_number', 'sale_id', 'branch_id', 'cashier',
    'reason', 'refund_method', 'refund_amount', 'status', 'created_at',
    # feat/shift-cash-drawer (schema v10): returns.session_id, same boot-time-
    # migration-vs-lazy-addcol ordering reasoning as SALES_TABLE_COLUMNS
    # above -- idempotency_key is also a lazy _ensure_credit_schema addcol.
    'session_id',
    # schema v13, same four columns and same ordering reasoning as
    # SALES_TABLE_COLUMNS above.
    'uid', 'actor_user_uid', 'terminal_id', 'created_at_utc',
    # Return-settlement split (fix(money) 78469b70): how the refund was
    # actually settled, so a refund is no longer counted as both drawer cash
    # and debt forgiveness. Deliberate and reviewed, which is this file's own
    # stated bar for touching a frozen literal.
    #
    # THESE SIT BEFORE `idempotency_key`, WHICH LOOKS WRONG AND IS NOT: they
    # are added by the boot-time migration chain, whereas idempotency_key is
    # a LAZY `_ensure_credit_schema` addcol that only runs on first use, so
    # it lands last in PRAGMA table_info order no matter when it was
    # introduced -- the same ordering note the session_id comment above
    # already makes.
    'tender_refund_amount', 'ar_forgiven_amount', 'store_credit_amount',
    # DAY-1 review F1 (schema v43 extension): the stored, line-exact tax a
    # return gave back. Deliberate and reviewed: unrelated to e-invoicing. It is
    # added by the boot-time chain, so (like the three above) it precedes the
    # lazy idempotency_key. This test still catches what it exists for -- an
    # e-invoicing migration touching a Retail table -- for every other column.
    'tax_refunded',
    'idempotency_key',
]
BASELINE_THREAD_NAMES = {'MainThread'}


def _make_admin_and_product(price=100.0, tax_rate=10.0, stock=50):
    email = f"einv-reg-{uuid.uuid4().hex[:10]}@test.local"
    password = "EinvRegPW1"
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
    # products.id is a client-generated TEXT UUID (multi-device sync
    # foundation), not an autoincrement integer -- unlike the old schema this
    # file was originally written against, a raw INSERT that omits id gets a
    # NULL primary key, not an auto-assigned one.
    product_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?, ?, 'ER-1','Regression Item',5,?,?)",
        (product_id, company_id, price, tax_rate),
    )
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (company_id, product_id, branch_id, stock),
    )
    rconn.commit()
    rconn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    client.get("/api/sub/retail/settings/tax")

    import random as _random
    dconn = get_retail_conn()
    dconn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
        (company_id, _random.randint(1, 5_000_000)),
    )
    dconn.commit()
    dconn.close()

    return client, company_id, product_id, branch_id


def test_sale_response_keys_unchanged():
    client, cid, pid, bid = _make_admin_and_product()
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    assert sorted(r.get_json()['data'].keys()) == sorted(SALE_RESPONSE_KEYS)


def test_sale_response_gains_einvoice_key_once_opted_into_tax_regime():
    """Companion to test_sale_response_keys_unchanged above, added
    2026-09-22 when 'einvoice' was removed from SALE_RESPONSE_KEYS -- that
    removal only moved this coverage, it did not drop it. A company that
    explicitly opts into tax_regime='jordan' (settings.py DEFAULTS
    ['tax_regime']='none' otherwise) must still see its sale enqueue, with
    the exact same response shape SALE_RESPONSE_KEYS used to pin
    unconditionally for every company.

    Opts in by writing the setting directly rather than POSTing to
    /api/einvoicing/settings -- see
    test_no_worker_thread_starts_for_a_company_with_no_enqueued_rows_at_boot's
    comment on the same choice: that live route starts a real worker
    thread the moment the resolved state flips to enabled, and this whole
    file (teardown_module never stops any worker) is written on the
    assumption that no test in it ever does that."""
    client, cid, pid, bid = _make_admin_and_product()
    from commercial_runtime.einvoicing import settings as _einvoice_settings
    _sconn = get_retail_conn()
    _einvoice_settings.set_setting(_sconn, cid, 'tax_regime', 'jordan')
    _sconn.commit()
    _sconn.close()
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert sorted(data.keys()) == SALE_RESPONSE_KEYS_WITH_EINVOICE
    assert data['einvoice']['status'] == 'queued'


def test_return_response_keys_unchanged():
    client, cid, pid, bid = _make_admin_and_product()
    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']
    r = client.post('/api/sub/retail/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': pid, 'quantity': 1}],
        'reason': 'regression check',
        'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    assert sorted(r.get_json()['data'].keys()) == sorted(RETURN_RESPONSE_KEYS)


def test_dashboard_stats_response_keys_unchanged():
    client, cid, pid, bid = _make_admin_and_product()
    r = client.get('/api/sub/retail/dashboard/stats')
    assert r.status_code == 200
    assert sorted(r.get_json()['data'].keys()) == sorted(DASHBOARD_DATA_KEYS)


def test_no_alter_ran_on_existing_tables():
    """Proves the einvoicing migration only added new tables -- it never
    touched an existing Retail table's column set.

    AUDIT 2026-09-22 -- fixes a real order-dependency, diagnosed rather than
    papered over: `sales.due_date` (see SALES_TABLE_COLUMNS' own comment)
    and `returns.idempotency_key` (see RETURNS_TABLE_COLUMNS') are LAZY
    columns api.retail_api._ensure_credit_schema() only adds the first time
    any request in this process reaches it (create_sale, create_return,
    etc.), gated on an in-process flag, not on `user_version`. This test
    used to rely on an earlier test in this same file (test_sale_response_
    keys_unchanged, which posts a real sale) having already triggered that
    lazy migration before this one ran -- so
    `pytest ...::test_no_alter_ran_on_existing_tables` in isolation failed
    with 'due_date' missing, a landmine this file never documented. Calling
    the same lazy migration directly makes this test prove its own claim
    regardless of what ran before it.
    """
    conn = get_retail_conn()
    _ensure_credit_schema(conn)
    assert [c[1] for c in conn.execute("PRAGMA table_info(sales)").fetchall()] == SALES_TABLE_COLUMNS
    assert [c[1] for c in conn.execute("PRAGMA table_info(sale_items)").fetchall()] == SALE_ITEMS_TABLE_COLUMNS
    assert [c[1] for c in conn.execute("PRAGMA table_info(customers)").fetchall()] == CUSTOMERS_TABLE_COLUMNS
    assert [c[1] for c in conn.execute("PRAGMA table_info(returns)").fetchall()] == RETURNS_TABLE_COLUMNS
    conn.close()


def test_einvoice_outbox_gets_exactly_one_row_for_the_sale_not_the_return():
    """AUDIT (2026-09-08): renamed from
    test_einvoice_outbox_stays_empty_through_a_full_sale_and_return_cycle.
    That claim described the OLD default (OFF) and was false by design at
    the time -- e-invoicing defaulted ON, so the sale in this same cycle
    enqueued. What's still true, and still worth a regression test: a
    return never enqueues anything of its own -- Retail has no credit-note/
    e-invoice handling for returns at all (grep core/retail/
    einvoice_adapter.py and retail_api.py's create_return: neither
    references an enqueue call), so exactly ONE outbox row -- the sale's --
    must exist after a sale+return cycle, not two and not zero.

    AUDIT 2026-09-22 -- SUPERSEDES "e-invoicing defaults ON" ABOVE: it is
    opt-in again (settings.py DEFAULTS['tax_regime']='none', see that key's
    own comment). This test now opts in explicitly, by writing the setting
    directly rather than POSTing to /api/einvoicing/settings -- see
    test_no_worker_thread_starts_for_a_company_with_no_enqueued_rows_at_boot's
    comment on why the live route is avoided in this file. Without opting
    in at all, `'einvoice' in sale` would fail before this test ever
    reaches the claim it actually exists to prove (that a return
    adds no row of its own).

    Scoped to THIS test's own company_id: einvoice_outbox has no company
    filter of its own, and other tests in this same module share one
    physical db across the whole process -- other tests that also opt into
    tax_regime='jordan' add their own rows to this same table, so an
    unscoped COUNT(*) here would assert on cross-test leakage, not on this
    test's own behavior."""
    client, cid, pid, bid = _make_admin_and_product(stock=10)
    from commercial_runtime.einvoicing import settings as _einvoice_settings
    _sconn = get_retail_conn()
    _einvoice_settings.set_setting(_sconn, cid, 'tax_regime', 'jordan')
    _sconn.commit()
    _sconn.close()
    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']
    assert 'einvoice' in sale, "the sale itself must have enqueued once opted into tax_regime='jordan'"
    client.post('/api/sub/retail/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': pid, 'quantity': 1}],
        'reason': 'regression check',
        'idempotency_key': str(uuid.uuid4()),
    })
    conn = get_retail_conn()
    rows = conn.execute(
        "SELECT source_type, source_id FROM einvoice_outbox WHERE company_id=?", (cid,)
    ).fetchall()
    conn.close()
    assert len(rows) == 1, "a return must never enqueue its own e-invoice/credit-note row (not implemented in Phase 1)"
    assert rows[0][0] == 'sale' and rows[0][1] == sale['id'], "the one row must be the sale itself, not the return"


def test_no_worker_thread_starts_for_a_company_with_no_enqueued_rows_at_boot():
    """Renamed and re-scoped 2026-09-08. This was
    test_no_background_thread_exists_when_feature_never_enabled, and BOTH
    halves of that name were false by the time anyone read it:

      * "never enabled" -- e-invoicing defaulted ON at the time
        (commercial_runtime/einvoicing/settings.py DEFAULTS 'enabled': '1'),
        which is why the test above this one asserted `'einvoice' in sale`
        unconditionally. Every company in that process had the feature on.
      * the old docstring's "trivially true, no worker module exists yet" --
        app.py:248 defines _get_or_create_einvoicing_worker and app.py:944
        starts real OutboxWorkers through _resume_einvoicing_workers().

    So the failure message was a statement about a fixture that does not
    exist, and the obvious next move -- starting the worker on enqueue --
    would have turned it red for doing the right thing.

    AUDIT 2026-09-22 -- SUPERSEDES "every company in this process has the
    feature on" ABOVE: e-invoicing is opt-in again (settings.py
    DEFAULTS['tax_regime']='none'). This test now opts this company in
    explicitly so the sale below still enqueues a real row -- the
    anti-vacuity check right after it already demands exactly that,
    unchanged from before.

    What is actually pinned here, and it is worth pinning: the boot sweep,
    _resume_einvoicing_workers() at app.py:952. It runs once at init_app()
    (import time, top of this file), selects companies with an explicit
    enabled='1' row UNIONed with companies that already have
    einvoice_outbox rows, and this process's database was empty at that
    moment -- so zero threads. The sale below then enqueues a real row and
    starts nothing, because enqueue_sale (retail_api.py:4816) writes the
    outbox and does not touch the worker registry.

    That is deliberate, not an oversight: the worker for a company enabled
    only by the default resumes at the NEXT boot, which is what the AUDIT
    note in _resume_einvoicing_workers' own docstring is about. If someone
    later starts the worker on enqueue instead, this test goes red and the
    right response is to update it, not to work around it -- the message
    below says so.

    Scope, stated plainly because threading.enumerate() is process-global
    while the fixture is company-scoped: this proves a fact about the whole
    process, not about `cid`. What it can no longer catch -- and never
    could -- is a worker started for ONE company while another company's
    stayed off."""
    client, cid, pid, bid = _make_admin_and_product()
    # AUDIT 2026-09-22: opt this company into tax_regime='jordan' by writing
    # the setting directly, NOT via POST /api/einvoicing/settings. That
    # route starts a real worker thread the moment the resolved state flips
    # to enabled (routes.py::_post_settings, `if now_enabled: get_worker
    # (cid).start(...)`) -- going through it here would start a genuine
    # e-invoicing worker thread and fail THIS test's own assertion (no
    # worker thread exists) for a reason that has nothing to do with the
    # boot sweep it exists to pin. Writing the setting directly satisfies
    # enqueue_sale's is_enabled() check below without touching the worker
    # registry at all, same as the boot sweep finding an empty outbox did
    # before this fix.
    from commercial_runtime.einvoicing import settings as _einvoice_settings
    _sconn = get_retail_conn()
    _einvoice_settings.set_setting(_sconn, cid, 'tax_regime', 'jordan')
    _sconn.commit()
    _sconn.close()
    client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })

    # Anti-vacuity. Without this the assertion below is satisfied just as
    # well by a sale that enqueued NOTHING -- which is precisely the state
    # the old version of this test silently measured, and the first failure
    # shape in ENGINEERING.md section 1.
    conn = get_retail_conn()
    queued = conn.execute(
        "SELECT COUNT(*) c FROM einvoice_outbox WHERE company_id=?", (cid,)
    ).fetchone()['c']
    conn.close()
    assert queued == 1, (
        f"fixture is not exercising the thing under test: {queued} outbox row(s) "
        "for this company. This check must run against a sale that really did "
        "enqueue, or 'no worker started' proves nothing."
    )

    names = {t.name for t in threading.enumerate()}
    assert names.issubset(BASELINE_THREAD_NAMES | {'MainThread'}), (
        "an e-invoicing worker thread is running in a process whose boot sweep "
        "(_resume_einvoicing_workers, app.py:952) found an empty outbox: "
        f"{names - BASELINE_THREAD_NAMES}. Either the boot sweep's gate "
        "regressed, or enqueueing now starts a worker -- if the latter is "
        "deliberate, rewrite this test to pin the new startup rule; do not "
        "widen BASELINE_THREAD_NAMES."
    )
