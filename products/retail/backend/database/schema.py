"""
Aura Retail -- database schema and connection.

Owns retail.db: branches, categories, products, inventory_movements,
inventory_balances, customers, suppliers, purchase_orders,
purchase_order_items, sales, sale_items, returns, return_items, payments,
tax_rates, journal_entries, audit_log. `retail_settings` and `doc_sequences`
are created lazily by api/retail_api.py's `_ensure_credit_schema` (unchanged
from source).

Extracted verbatim (schema + seed) from Action Aura Enterprise's
database/subsystem_db.py `init_retail`/`_seed_retail` (lines 4732-4941), which
also owned schema for every other subsystem in that monolith -- see
docs/migration/dependency-map.md §1. Only the retail-relevant slice plus the
generic connection helpers it needs are kept here.
"""
import os
import sqlite3
import random
from datetime import datetime, timedelta, timezone

_app_data = os.environ.get('AURA_APP_DATA')
if _app_data:
    BASE_DIR = os.path.join(_app_data, 'database')
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SUBSYS_DIR = os.path.join(BASE_DIR, 'subsystems')

# Wave 1B (Part K): see products/clinic/backend/database/schema.py's
# identical CLINIC_SCHEMA_VERSION for the full rationale.
# Multi-device sync foundation (2026-08-06), v1 -> v2: categories.id moves
# from INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY (client-
# generated UUID) so two offline devices can create categories without ever
# colliding on id. See _migrate_categories_to_uuid below.
# Multi-device sync foundation (2026-08-07), v2 -> v3: products.category_id's
# FOREIGN KEY gains ON DELETE SET NULL. See
# _migrate_products_category_fk_on_delete_set_null below for why a bare
# `REFERENCES categories(id)` permanently wedges sync on any device holding a
# product in a category some OTHER device deleted.
# Multi-device sync foundation (2026-08-07), v3 -> v4: products.id moves from
# INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY (client-generated
# UUID), same reason categories got this in v1 -> v2: two offline devices
# must never collide on an id. Every table with a declared FK to products(id)
# -- inventory_movements, inventory_balances, sale_items,
# purchase_order_items, return_items -- is rebuilt in the same transaction
# and has its product_id values remapped. See _migrate_products_to_uuid below.
# Still v4: customers.id gets the identical INTEGER -> TEXT UUID treatment,
# plus a new `status` column (customers never had one) and the credit_mode/
# credit_limit/credit_balance columns folded in as first-class columns. No
# table has a declared FK to customers(id) (sales.customer_id and
# payments.party_id are both loose, undeclared columns), so this rides along
# on the same v4 bump rather than needing one of its own. See
# _migrate_customers_to_uuid below.
# Still v4: suppliers.id gets the identical INTEGER -> TEXT UUID treatment,
# plus the payment_terms/credit_balance columns _ensure_credit_schema adds at
# runtime folded in as first-class columns. Unlike customers, this table DOES
# have a declared FK pointing at it (purchase_orders.supplier_id
# REFERENCES suppliers(id)), so this migration carries the same rename-away
# hazard as products/categories -- see _migrate_suppliers_to_uuid below.
# v5: adds products.supplier_id (TEXT, referencing suppliers(id) which is
# already UUID by this point) -- see _migrate_products_add_supplier_fk below.
# v6: PO-preview-by-supplier foundation (Thursday demo, Stream B). Adds
# supplier_contacts (a supplier can have several named contacts -- orders/
# accounts/general -- each with its own channel), plus split-group/routing/
# idempotency columns on purchase_orders (the writers that populate them land
# later this week; this bump only adds the columns) and suppliers.
# min_order_value (used by the split preview's MOQ warning). Pure additive
# migration -- ADD COLUMN + CREATE TABLE only, nothing renamed or retyped, so
# unlike v1-v5 this needs no _new-table rebuild. See
# _migrate_add_supplier_contacts_and_po_split below.
# v7 (docs/einvoicing/phase1/): adds the einvoice_* tables used by opt-in
# Jordan JoFotara e-invoicing -- CREATE TABLE IF NOT EXISTS only, nothing
# existing is ALTERed, read, or written. This arrived on master as ITS v2
# (master numbered v1 -> v2 einvoicing -> v3 products.supplier_id INTEGER)
# while this lineage independently ran v1 -> v6; the two histories were
# merged on 2026-08-10 (feat/retail-mobile-build-baseline). This lineage's
# numbering wins and master's v2/v3 are superseded, never re-run: every step
# in _migrate_retail_schema gates on the LIVE schema (PRAGMA table_info /
# foreign_key_list), never on the version integer, so a database carrying
# master's v3 marker migrates correctly even though "3" meant something
# different there. The bump to 7 is load-bearing, not cosmetic:
# ensure_schema_version returns early on current >= target, so a database
# already at 6 from this lineage would NEVER receive the einvoice_* tables
# if this stayed at 6.
# v8 (reorder automation foundation, feat/reorder-automation-foundation):
# adds products.reorder_method (TEXT DEFAULT 'none' -- per-product opt-in;
# an install that never sets this on any product gets zero behavior change)
# and the reorder_requests table (client-generated UUID id, same convention
# as every other sync-eligible entity since the multi-device sync
# foundation -- never autoincrement, so this table CAN be synced through
# Owner's relay, unlike purchase_orders; see
# _migrate_add_reorder_automation_foundation below for the full reasoning).
# Pure additive migration -- ADD COLUMN + CREATE TABLE only, same shape as
# v5/v6, so no _new-table rebuild is needed here either.
# v9 (feat/email-outbox-foundation): adds the email_* tables owned by
# commercial_runtime/notifications -- CREATE TABLE IF NOT EXISTS only,
# nothing existing is ALTERed, read, or written. Same "opt-in, invisible
# until configured" shape as v7's einvoice_* addition: an install that never
# sets AURA_SMTP_HOST (commercial_runtime/notifications/smtp_client.py) or
# any company's `email_settings.enabled` gets zero behavior change -- the
# reorder-automation post-sale hook (core/retail/reorder_hook.py) now also
# queues a low-stock alert email, but only when both of those are true, so
# an install that never configures either one sees byte-for-byte the same
# reorder_requests-only behavior v8 already shipped.
# v10 (feat/shift-cash-drawer): real shift / cash-drawer management -- cash
# float in/out tracking plus X (mid-shift, non-destructive) and Z (end-of-
# shift, locking) reports. Two brand-new tables only, same additive shape as
# v8/v9 (CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS, nothing
# existing dropped or retyped):
#   - `cash_sessions`: one row per opened-to-closed till session
#     (company_id/branch_id-scoped, id is a client-generated UUID -- same
#     convention as reorder_requests/supplier_contacts, never autoincrement,
#     so this table CAN ride the sync outbox later without hitting the
#     purchase_orders-style UUID-parse wall reorder_requests' own v8 comment
#     explains). A partial UNIQUE index (idx_cash_sessions_one_open_per_branch)
#     enforces "at most one OPEN session per branch" as a real constraint,
#     mirroring idx_reorder_requests_open's technique exactly.
#   - `cash_movements`: one row per float-in/float-out/paid-in/paid-out event
#     against an open session. Scoped ONLY via session_id (no company_id/
#     branch_id of its own) -- a movement has no meaning outside the session
#     it belongs to, so the FK is the only scope it needs, same shape as
#     sale_items scoping through sale_id rather than repeating company_id.
# PLUS two nullable additive columns -- `sales.session_id` and
# `returns.session_id` (both TEXT, REFERENCES cash_sessions(id)) -- so a
# completed sale/return can be attributed to the exact till session it
# happened in. Stamped at write time in retail_api.py::create_sale /
# create_return via the new `_open_cash_session_id()` helper, which NEVER
# raises and defaults to NULL on any failure -- an install that never opens a
# cash session sees byte-for-byte the same sales/returns behavior as before
# this migration (see retail_cash_drawer_regression_test.py). This is a
# direct FK stamp, not a branch+time-range lookup at report time -- a
# time-range query silently breaks the instant a session spans midnight, or
# whenever two sessions on the same branch sit back-to-back with no visible
# gap between them; a stamped id is unambiguous regardless of wall-clock
# weirdness (DST, backdated imports, clock skew across devices), the same
# reasoning `sync_outbox` already relies on entity ids for, never timestamps,
# to identify what a relayed event is actually about.
# v11 (feat/pos-hold-resume-sale): adds the held_sales table (park/resume a
# sale on the POS screen). CREATE TABLE IF NOT EXISTS only -- no existing
# table is ALTERed, read, or written, same "pure additive" shape as v8/v9.
#
# *** RENUMBERED AT INTEGRATION TIME, TWICE -- this branch originally
# shipped as v4, cut from an old point of feat/retail-mobile-build-baseline
# (RETAIL_SCHEMA_VERSION was 3 there at the time; see git history on
# feat/pos-hold-resume-sale for that original commit's own collision-risk
# note, which correctly predicted this renumbering would be needed). The
# ROADMAP.md ledger (2026-08-12) reserved v9 for this feature, but
# feat/email-outbox-foundation claimed v9 first (merged into this lineage
# ahead of held-sales landing) -- verified directly by reading this file at
# feat/email-outbox-foundation's tip before renumbering, not assumed from
# the ledger alone. This branch was first rebased onto that v9 tip and
# renumbered to v10 -- but before that landed, feat/shift-cash-drawer
# (commit 41d24cf, cash_sessions/cash_movements/X-Z reports) independently
# claimed v10 for real, directly on the SAME v9 base (83b5bcb), and that
# commit exists with its own passing tests. So v10 is now taken by a
# sibling branch this task is explicitly scoped to never touch or merge
# with -- v11 is the actual next-free number, verified the same way (read
# feat/shift-cash-drawer's schema.py directly, not inferred). There is
# consequently a deliberate GAP at v10 in THIS branch's own migration
# chain -- it never claims or runs anything for "v10" because that number
# belongs to a different, unmerged branch's schema change. This is
# harmless: ensure_schema_version only cares that the target integer is
# higher than whatever a real database's PRAGMA user_version currently is,
# never that every integer up to it was independently used by THIS
# lineage (see this file's own v1-v3 vs. master's superseded v2/v3
# numbering, above, for the precedent). Whoever eventually reconciles
# feat/pos-hold-resume-sale with feat/shift-cash-drawer at real merge time
# will need to pick a final relative order and may renumber one of them
# again -- that is their decision, not made here.
#
# `held_sales.customer_id` is TEXT here (not the original commit's INTEGER)
# to match `customers.id`, which is UUID TEXT on this lineage since the
# multi-device sync foundation's v1->v4 migrations (see
# _migrate_customers_to_uuid below) -- an INTEGER-declared column would
# still physically store a UUID string fine (SQLite column affinity is
# soft), but a declared type that lies about the real shape of the data is
# a bug waiting to bite the next reader/writer, so it's fixed here rather
# than carried forward. See _migrate_add_held_sales below for the
# corresponding table definition and subsystem-retail.js's _saveHold() for
# the matching frontend fix (was `+customerId`, a numeric coercion that
# silently turned every UUID customer id into NaN -> null -- the exact
# `+`-coercion-on-an-id-field bug pattern ROADMAP.md's 2026-08-12 entry
# flagged whoever reconciled this branch to grep for).
# v11 -> v12 (feat/retail-mobile-build-baseline, whatsapp-recipients):
# adds whatsapp_recipients (commercial_runtime/notifications/schema.py::
# apply_whatsapp_recipients_schema) -- multi-recipient, role/branch-scoped
# WhatsApp report routing. Pure additive CREATE TABLE IF NOT EXISTS, same
# shape as every notifications-owned table before it; no existing table is
# touched. See _migrate_add_whatsapp_recipients below.
# v12 -> v13 (launch-readiness Phase 2, ROADMAP.md 2026-08-21 reservation):
# identity and attribution columns. THE FIRST MIGRATION IN THIS FILE THAT
# ALTERS THE TABLES HOLDING A REAL SHOP'S SALES HISTORY -- every step before
# it either created new tables (v6-v12) or rebuilt catalogue/party tables
# (v1-v5). It is consequently additive ONLY: ALTER TABLE ADD COLUMN,
# CREATE [UNIQUE] INDEX IF NOT EXISTS and guarded UPDATEs, never the
# DROP+RENAME rebuild _migrate_products_to_uuid uses. That rebuild's risk
# profile is deliberately not repeated here: it copies every row through a
# fresh table with PRAGMA foreign_keys temporarily OFF, and a mistake in the
# column list silently drops real data with no error and a passing
# integrity_check -- see _legacy_supplier_link's docstring for a case where
# exactly that happened on this very lineage. Three groups of columns:
#   - `uid` + a partial UNIQUE index on branches/sales/sale_items/returns/
#     return_items/inventory_movements/payments. The WIRE identity, exactly
#     as registry v3 gave `users` one (commercial_runtime/identity/
#     account_schema.py). The local autoincrement `id` stays the primary key
#     and stays a private detail of this install.
#   - `actor_user_uid`/`terminal_id`/`created_at_utc` on sales/returns/
#     inventory_movements/cash_sessions/cash_movements -- WHO, WHERE and
#     WHEN-in-real-time, as three structured columns beside (never instead
#     of) the existing free-text cashier/created_by/opened_by.
#   - `row_version`/`updated_at_utc`/`deleted_at_utc` on the four catalogue
#     tables (categories/products/customers/suppliers) and reorder_requests
#     -- the reject-stale marker plus a soft tombstone, matching registry
#     v3's identical triple on `users`.
# v13 -> v14 (same reservation): the company_id rebind. `company_id` is
# derived once, locally, at onboarding as md5(admin_email)
# (commercial_runtime/identity/onboarding_routes.py::create_admin), which is
# a pure function of an email address and therefore means nothing to Owner.
# Owner's relay scopes every sync event by `license_id`
# (owner/app/sync/routes.py: `SyncEvent.license_id == license_id`, resolved
# server-side from the VERIFIED installation, never from body input), and
# that same value is what Owner signs into every assertion as
# `license_public_id` (owner/app/licensing_service/assertions.py:
# `"license_public_id": str(license_row.id)`). So the Owner-issued tenant key
# this database has to converge on is `license_public_id`, and v14 is the
# ability to move onto it. Nothing about v14 requires a licence to exist:
# licensing is OFF by default in this product (config.py -- with
# OWNER_LICENSING_BASE_URL unset the app runs fully unlocked), so the common
# case is an install that reaches v14 long before it ever activates. v14
# therefore advances unconditionally and the rebind is a no-op when there is
# nothing to rebind to. See _migrate_rebind_company_id_to_owner_issued and
# rebind_company_id below.
# v14 -> v15 (launch-readiness Phase 3, docs/launch-readiness/
# phase3-ledger-truth.md, ROADMAP.md's 2026-08-21 reservation): the ledger
# becomes able to reproduce the cache. `inventory_balances.quantity_on_hand`
# is a STORED number five writers keep in step with `inventory_movements` by
# hand; core/retail/stock_reconciliation.py can already MEASURE the two
# disagreeing, but on a pre-existing install the ledger simply has no rows
# behind most balances at all -- the stock was there before the movement
# table was ever written to, or it arrived through the importer's absolute
# SET, or through _seed_retail. v15 seeds ONE `opening_count` movement per
# unexplained key carrying the RESIDUAL -- quantity_on_hand minus whatever
# the ledger already accounts for there -- and then GATES: it recomputes
# drift afterwards and refuses to advance the version marker if any
# REPAIRABLE row still disagrees. That gate is the point of the step; the
# seeding is only what makes passing it possible. Phase 5 (devices exchanging
# movements and each recomputing its own balances) is unsafe without it -- on
# an install whose ledger cannot reproduce its balances, sync replaces one
# correct-looking number with a different correct-looking number and nobody
# can say which was right. Four consequences that look like details and are
# not:
#   - THE RESIDUAL, not the absence of history. The seeding originally
#     covered only keys with ZERO movements, and that skipped the COMMON
#     legacy key: the importer wrote an absolute SET with no movement row
#     (git 13c1c92, api/import_api.py:1146) and every sale since wrote one,
#     so the ordinary shop has sales and no opening. It was refused, the POS
#     could not boot (app.py calls init_retail() unconditionally), and the
#     refusal's own recovery advice then turned shelves of [112, 57, 7] into
#     [-8, -3, -1]. An opening count IS the residual by definition; the
#     zero-movement case is its special case.
#   - "repairable" excludes the two STRUCTURAL impossibilities: a movement
#     whose branch_id is NULL (inventory_balances.branch_id is NOT NULL, so
#     no balance row could hold it) and a balance whose product row is gone
#     (inventory_movements.product_id has an enforced FK, so no movement row
#     can be written for it). A gate demanding GLOBAL zero drift wedges such
#     an install out of EVERY future migration -- the permanent wedge the v13
#     duplicate-uid defect produced, reached from two more directions. So
#     NULL-branch rows are resolved where the answer is unambiguous (a
#     company with exactly one branch has only one place they can belong),
#     and what stays ambiguous -- along with every orphan balance -- is
#     counted, recorded and excluded. Such an install STILL ADVANCES, with
#     visible queues instead of a guess or a brick.
#   - the gate still REFUSES a negative residual: the ledger accounting for
#     more goods than the shelf shows is not something an opening count can
#     explain, and seeding it anyway would make the gate incapable of
#     failing.
#   - the seeding is COMMITTED before the gate can raise, and
#     stock_reconciliation.repair_drift refuses to LOWER any balance until
#     this gate has passed. See _migrate_seed_opening_counts_and_gate_drift
#     for why either one alone leaves the documented recovery able to wipe a
#     shop's stock.
RETAIL_SCHEMA_VERSION = 15

# ── v13: which table gets which group of columns ────────────────────────────
# Kept as module constants rather than inlined into the migration so the
# report/query code that lands on top of these columns can import the same
# lists instead of re-deriving them (and drifting from them).

#: Tables that gain the wire identity `uid` + a partial UNIQUE index.
RETAIL_UID_TABLES = (
    'branches', 'sales', 'sale_items', 'returns', 'return_items',
    'inventory_movements', 'payments',
)

#: Tables that gain actor_user_uid / terminal_id / created_at_utc.
RETAIL_ACTOR_TABLES = (
    'sales', 'returns', 'inventory_movements', 'cash_sessions', 'cash_movements',
)

#: Tables that gain row_version / updated_at_utc / deleted_at_utc.
RETAIL_ROW_VERSION_TABLES = (
    'categories', 'products', 'customers', 'suppliers', 'reorder_requests',
)

#: How many rows the v13 uid backfill materialises in Python at a time. See
#: _migrate_add_identity_and_attribution_columns -- `sale_items` on a shop
#: with years of history is far and away the biggest table here, and this
#: migration runs on a till machine, not a server.
_UID_BACKFILL_CHUNK = 5000


class RetailUidIndexError(Exception):
    """Raised when v13 cannot leave `idx_<table>_uid` in the one shape that
    makes the column mean anything -- a UNIQUE, partial index on `uid`.

    Deliberately NOT caught anywhere. The alternative to raising is advancing
    `user_version` while uid uniqueness is quietly absent, which is the F4
    defect itself: no error, no signal, and the wire identity silently stops
    being an identity. Raising re-runs the whole (idempotent) chain on the
    next launch, repair attempt included, so it is a retry rather than the
    permanent wedge an unguarded `CREATE UNIQUE INDEX` produced -- see
    `_ensure_unique_uid_index` for why reaching this is close to impossible
    once the duplicate repair has run.
    """


class RetailLedgerDriftError(Exception):
    """Raised by v15 when this install's `inventory_balances` cannot be
    reproduced from `inventory_movements` even after the opening counts are
    seeded -- i.e. some (product, branch) whose ledger accounts for MORE
    stock than the balance claims, which no inferred opening count can
    explain because a shop cannot have started with less than nothing.

    Deliberately NOT caught anywhere, and deliberately not softened into a
    warning. `ensure_schema_version` advances `PRAGMA user_version` only when
    `migrate_fn` returns, so raising is the mechanism by which v15 refuses to
    call the cache derivable when it demonstrably is not. A shop that reaches
    this has a real, measurable inconsistency between what its shelves are
    said to hold and what its own recorded history can account for -- the
    exact condition Phase 3 exists to surface, and the one Phase 5 must never
    sync across devices.

    The message names the offending rows and the recovery, because "migration
    failed" is not an actionable report about a shop's stock. Recovery is the
    EXPLICIT, owner-initiated repair (core/retail/stock_reconciliation.py::
    repair_drift), which on these rows RAISES the balance to the total the
    ledger can prove arrived and so destroys no stock. Two things make it
    safe to point an operator at, and the first one alone was not enough:
    v15 commits its seeded opening counts BEFORE this can be raised, AND
    `repair_drift` refuses to lower any balance until this gate has passed.
    See `_migrate_seed_opening_counts_and_gate_drift`.

    THE REFUSAL BLOCKS THE APP FROM STARTING -- `app.py` calls
    `init_retail()` with no handler -- so what reaches it must be worth that.
    After the residual fix it is: the ordinary legacy shop is seeded and
    advances, the two structurally unreconcilable classes are excluded, and
    what is left is a shop whose recorded history and shelves genuinely
    contradict each other in the one direction no inference can resolve.
    """


def _get_path(name):
    os.makedirs(SUBSYS_DIR, exist_ok=True)
    return os.path.join(SUBSYS_DIR, f'{name}.db')


def _conn(name):
    c = sqlite3.connect(_get_path(name), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    # Wave 0 (AUDIT-016): every declared FOREIGN KEY in this schema was
    # previously decorative -- SQLite defaults enforcement to OFF, and this
    # was the only connection factory in the file, so it was never turned on
    # anywhere. Set on every connection (SQLite does not persist this setting
    # in the database file itself; it must be set per-connection, every time).
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _is_standalone():
    """Return True when running as a packaged customer build (no seed data)."""
    try:
        import config as _cfg
        return getattr(_cfg, 'IS_STANDALONE', False)
    except ImportError:
        return False


def get_retail_conn():
    return _conn('retail')


def sub_create(conn_fn, table, data):
    conn = conn_fn()
    try:
        keys = ', '.join(data.keys())
        placeholders = ', '.join(['?'] * len(data))
        conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", list(data.values()))
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def _migrate_categories_to_uuid(conn):
    """One-time migration (schema v1 -> v2): categories.id moves from
    INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY (client-generated
    UUIDs), so two offline devices can create categories without ever
    colliding on id. products.category_id is repointed to match, preserving
    every existing row (name/description/company_id/created_at, and every
    product's category link, including NULL).

    Two SQLite behaviours (both confirmed empirically against this exact
    connection factory/pragmas, not assumed) shaped how this is written:

    1. `PRAGMA foreign_keys=ON` is set on every connection by `_conn()`
       above, including the one this runs on. Left on, `DROP TABLE
       categories_old` (once products rows reference it) and `ALTER TABLE
       products DROP COLUMN category_id_old` (SQLite refuses to drop a
       column that is part of a FOREIGN KEY definition) both fail partway
       through a naive rename-based rebuild. So this runs with
       foreign_keys temporarily OFF (it cannot be toggled inside a
       transaction, so it's flipped before BEGIN and restored after COMMIT
       via try/finally).

    2. `ALTER TABLE x RENAME TO y` auto-rewrites any OTHER table's
       FOREIGN KEY clause that points at `x`, to point at `y` instead
       (SQLite's default legacy_alter_table=OFF behaviour). Renaming
       `categories` to `categories_old` would silently rewrite products'
       declared FK to `REFERENCES "categories_old"(id)`; renaming
       `products` away would do the same to inventory_movements/
       sale_items/purchase_order_items/return_items. Both tables are
       instead rebuilt under a `_new` name and swapped in via
       `DROP <original>` + `RENAME <new> TO <original>` -- the original
       name is never renamed away, so no other table's FK text is ever
       touched.

    The whole rebuild runs as one explicit transaction; any failure rolls
    back completely (verified: this Python/SQLite combination honors
    explicit BEGIN across mixed DDL+DML), leaving the database exactly as
    the pre-migration backup captured it for a clean retry on next launch.
    """
    import uuid as _uuid

    # Defensive idempotency: ensure_schema_version's version gate is the
    # normal guard against a second run, but check directly too rather than
    # relying solely on that.
    id_col = next((c for c in conn.execute("PRAGMA table_info(categories)").fetchall() if c["name"] == "id"), None)
    if id_col is not None and id_col["type"].upper() == "TEXT":
        return

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")

        legacy_supplier_links = _legacy_supplier_link(conn)

        cat_rows = conn.execute(
            "SELECT id, company_id, name, description, created_at FROM categories"
        ).fetchall()
        id_map = {row["id"]: str(_uuid.uuid4()) for row in cat_rows}

        conn.execute("""
            CREATE TABLE categories_new (
                id TEXT PRIMARY KEY,
                company_id INTEGER DEFAULT 1,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for row in cat_rows:
            conn.execute(
                "INSERT INTO categories_new (id, company_id, name, description, created_at) VALUES (?,?,?,?,?)",
                (id_map[row["id"]], row["company_id"], row["name"], row["description"], row["created_at"]),
            )
        conn.execute("DROP TABLE categories")
        conn.execute("ALTER TABLE categories_new RENAME TO categories")

        conn.execute("""
            CREATE TABLE products_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER DEFAULT 1,
                sku TEXT NOT NULL,
                barcode TEXT,
                name TEXT NOT NULL,
                category_id TEXT,
                cost_price REAL DEFAULT 0,
                sell_price REAL DEFAULT 0,
                tax_rate REAL DEFAULT 0,
                unit TEXT DEFAULT 'pcs',
                reorder_level INTEGER DEFAULT 5,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
            )
        """)
        conn.execute("""
            INSERT INTO products_new (id, company_id, sku, barcode, name, category_id,
                                       cost_price, sell_price, tax_rate, unit, reorder_level,
                                       status, created_at)
            SELECT id, company_id, sku, barcode, name, NULL,
                   cost_price, sell_price, tax_rate, unit, reorder_level,
                   status, created_at
            FROM products
        """)
        old_cat_ids = conn.execute(
            "SELECT DISTINCT category_id FROM products WHERE category_id IS NOT NULL"
        ).fetchall()
        for row in old_cat_ids:
            old_cid = row["category_id"]
            new_cid = id_map.get(old_cid)
            if new_cid is not None:
                conn.execute(
                    "UPDATE products_new SET category_id=? WHERE id IN "
                    "(SELECT id FROM products WHERE category_id=?)",
                    (new_cid, old_cid),
                )
        conn.execute("DROP TABLE products")
        conn.execute("ALTER TABLE products_new RENAME TO products")

        _restore_supplier_link(conn, legacy_supplier_links)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")


def _legacy_supplier_link(conn):
    """Snapshot products.supplier_id as {product_id: supplier_id}, or None
    when that column does not exist on the LIVE products table.

    Merge safety (feat/retail-mobile-build-baseline, 2026-08-10): master and
    this sync-foundation lineage each independently invented a
    products.supplier_id column -- master's as
    `INTEGER REFERENCES suppliers(id)` at ITS schema v3 (when suppliers.id was
    still INTEGER), this lineage's as `TEXT REFERENCES suppliers(id)` at v5,
    after _migrate_suppliers_to_uuid made suppliers.id a UUID. See
    ROADMAP.md's supplier_id timing note.

    A database that ran master's v3 therefore reaches this chain holding real,
    user-entered supplier links in a column that EVERY products rebuild below
    recreates from a FIXED column list -- _migrate_categories_to_uuid,
    _migrate_products_category_fk_on_delete_set_null and
    _migrate_products_to_uuid all do `CREATE TABLE products_new (...)` +
    `DROP TABLE products`. Without this helper the column and all of its
    values are silently dropped on the way to v7 and
    _migrate_products_add_supplier_fk then re-adds it empty: verified against
    the real code, every product's supplier link came back NULL, with no
    error and no integrity_check failure. Each rebuild snapshots the column
    here and restores it via _restore_supplier_link below;
    _migrate_suppliers_to_uuid then remaps the surviving values from master's
    integer supplier ids to the new supplier UUIDs, exactly as it already
    does for purchase_orders.supplier_id and payments.party_id.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
    if "supplier_id" not in cols:
        return None
    return {
        row["id"]: row["supplier_id"]
        for row in conn.execute("SELECT id, supplier_id FROM products").fetchall()
    }


def _restore_supplier_link(conn, links, id_map=None):
    """Re-add products.supplier_id after a rebuild and put `links` back.

    `id_map` maps old products.id -> new products.id for the one rebuild that
    also changes the primary key (_migrate_products_to_uuid); pass None when
    the rebuild preserved ids verbatim. The column is declared with exactly
    the DDL _migrate_products_add_supplier_fk uses, so a database that came
    through master's v3 ends with a byte-identical `products` definition to a
    fresh install's. No-ops when `links` is None, i.e. on this lineage's own
    upgrade path where the column does not exist yet at rebuild time.
    """
    if links is None:
        return
    conn.execute("ALTER TABLE products ADD COLUMN supplier_id TEXT REFERENCES suppliers(id)")
    for old_pid, supplier_id in links.items():
        if supplier_id is None:
            continue
        new_pid = old_pid if id_map is None else id_map.get(old_pid)
        if new_pid is None:
            continue
        conn.execute("UPDATE products SET supplier_id=? WHERE id=?", (supplier_id, new_pid))


def _products_category_fk_is_set_null(conn):
    """True when products.category_id's FOREIGN KEY already declares
    ON DELETE SET NULL. Read from SQLite's own `PRAGMA foreign_key_list`
    (the parsed FK definition) rather than by string-matching the stored
    CREATE TABLE text, so formatting/whitespace differences between the base
    schema and a migrated rebuild can never make this misreport."""
    try:
        rows = conn.execute("PRAGMA foreign_key_list(products)").fetchall()
    except sqlite3.DatabaseError:
        return False
    for r in rows:
        if r["table"] == "categories" and r["from"] == "category_id":
            return (r["on_delete"] or "").upper() == "SET NULL"
    return False


def _migrate_products_category_fk_on_delete_set_null(conn):
    """One-time migration (schema v2 -> v3): products.category_id's FOREIGN
    KEY gains ON DELETE SET NULL.

    Why this is a correctness fix and not cosmetics: `_conn()` above sets
    `PRAGMA foreign_keys=ON` on EVERY connection, so a bare
    `REFERENCES categories(id)` is genuinely enforced. Categories are synced
    across devices on one license; products are NOT (this phase). Two devices
    therefore legitimately hold different product -> category assignments.
    Device A (no products in "Electronics") deletes that category and the
    delete is relayed; device B (which does have a product there) applies it
    via `commercial_runtime/sync/sync_service.py::_apply_event`'s
    `DELETE FROM categories WHERE id=?` and gets
    `IntegrityError: FOREIGN KEY constraint failed`. That aborts
    `apply_pull_result` before the cursor is advanced, and `run_once`
    swallows the exception -- so device B silently stops receiving ANY event
    from ANY device, permanently, with no self-healing. ON DELETE SET NULL
    makes the delete unassign B's products instead, which is also exactly
    the desktop UX intent for deleting a category locally.

    Structure mirrors `_migrate_categories_to_uuid` above (read its docstring
    for the full reasoning) and inherits its two safety properties verbatim:

    1. Runs with `PRAGMA foreign_keys` temporarily OFF (it cannot be toggled
       inside a transaction, so it is flipped before BEGIN and restored after
       COMMIT via try/finally) -- otherwise `DROP TABLE products` fails while
       inventory_movements/inventory_balances/purchase_order_items/
       sale_items/return_items rows still reference it.

    2. The original `products` name is never renamed away: the replacement is
       built as `products_new` and swapped in via `DROP products` +
       `RENAME products_new TO products`. Renaming `products` to
       `products_old` would make SQLite silently rewrite all five referencing
       tables' FK clauses to point at `products_old`.

    Idempotent (returns immediately when the FK is already SET NULL, on top
    of ensure_schema_version's user_version gate) and fully transactional:
    any failure rolls the whole rebuild back, leaving the database exactly as
    the pre-migration backup captured it for a clean retry on next launch.
    Every products row and every column value is preserved as-is -- this
    changes only the table's declared FK action, never any data.
    """
    if _products_category_fk_is_set_null(conn):
        return

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        legacy_supplier_links = _legacy_supplier_link(conn)
        conn.execute("""
            CREATE TABLE products_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER DEFAULT 1,
                sku TEXT NOT NULL,
                barcode TEXT,
                name TEXT NOT NULL,
                category_id TEXT,
                cost_price REAL DEFAULT 0,
                sell_price REAL DEFAULT 0,
                tax_rate REAL DEFAULT 0,
                unit TEXT DEFAULT 'pcs',
                reorder_level INTEGER DEFAULT 5,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
            )
        """)
        conn.execute("""
            INSERT INTO products_new (id, company_id, sku, barcode, name, category_id,
                                       cost_price, sell_price, tax_rate, unit, reorder_level,
                                       status, created_at)
            SELECT id, company_id, sku, barcode, name, category_id,
                   cost_price, sell_price, tax_rate, unit, reorder_level,
                   status, created_at
            FROM products
        """)
        conn.execute("DROP TABLE products")
        conn.execute("ALTER TABLE products_new RENAME TO products")
        _restore_supplier_link(conn, legacy_supplier_links)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")


def _migrate_products_to_uuid(conn):
    """One-time migration (schema v3 -> v4): products.id moves from
    INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY (client-generated
    UUIDs), same reason categories got this in v1->v2: two offline devices
    must never collide on an id. Every table with a declared FK to
    products(id) -- inventory_movements, inventory_balances, sale_items,
    purchase_order_items, return_items -- is rebuilt in this SAME
    transaction and its product_id values remapped via an old-id -> new-uuid
    map, mirroring exactly how _migrate_categories_to_uuid remaps
    products.category_id (see that function's docstring for the full
    PRAGMA foreign_keys / rename-hazard reasoning, inherited verbatim here).

    Sales/Inventory/Returns/Payments are NOT synced this phase (see the
    design spec's Scope section) -- only their product_id VALUES are
    remapped here, as a one-time local consequence of products.id changing
    type. This migration runs identically on every device independently;
    it does not require or wait for any other device.
    """
    import uuid as _uuid

    id_col = next((c for c in conn.execute("PRAGMA table_info(products)").fetchall() if c["name"] == "id"), None)
    if id_col is not None and id_col["type"].upper() == "TEXT":
        return

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")

        legacy_supplier_links = _legacy_supplier_link(conn)

        prod_rows = conn.execute(
            "SELECT id, company_id, sku, barcode, name, category_id, cost_price, sell_price, "
            "tax_rate, unit, reorder_level, status, created_at FROM products"
        ).fetchall()
        id_map = {row["id"]: str(_uuid.uuid4()) for row in prod_rows}

        conn.execute("""
            CREATE TABLE products_new (
                id TEXT PRIMARY KEY,
                company_id INTEGER DEFAULT 1,
                sku TEXT NOT NULL,
                barcode TEXT,
                name TEXT NOT NULL,
                category_id TEXT,
                cost_price REAL DEFAULT 0,
                sell_price REAL DEFAULT 0,
                tax_rate REAL DEFAULT 0,
                unit TEXT DEFAULT 'pcs',
                reorder_level INTEGER DEFAULT 5,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
            )
        """)
        for row in prod_rows:
            conn.execute(
                "INSERT INTO products_new (id, company_id, sku, barcode, name, category_id, cost_price, "
                "sell_price, tax_rate, unit, reorder_level, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (id_map[row["id"]], row["company_id"], row["sku"], row["barcode"], row["name"],
                 row["category_id"], row["cost_price"], row["sell_price"], row["tax_rate"],
                 row["unit"], row["reorder_level"], row["status"], row["created_at"]),
            )
        conn.execute("DROP TABLE products")
        conn.execute("ALTER TABLE products_new RENAME TO products")

        referencing = [
            ("inventory_movements", """
                CREATE TABLE inventory_movements_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, product_id TEXT NOT NULL,
                    branch_id INTEGER, movement_type TEXT NOT NULL, quantity REAL NOT NULL, unit_cost REAL DEFAULT 0,
                    reference TEXT, notes TEXT, created_by TEXT DEFAULT 'System', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES products(id)
                )""", "id, company_id, product_id, branch_id, movement_type, quantity, unit_cost, reference, notes, created_by, created_at"),
            ("inventory_balances", """
                CREATE TABLE inventory_balances_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, product_id TEXT NOT NULL,
                    branch_id INTEGER NOT NULL, quantity_on_hand REAL DEFAULT 0, quantity_reserved REAL DEFAULT 0,
                    UNIQUE(company_id, product_id, branch_id), FOREIGN KEY (product_id) REFERENCES products(id)
                )""", "id, company_id, product_id, branch_id, quantity_on_hand, quantity_reserved"),
            ("sale_items", """
                CREATE TABLE sale_items_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, sale_id INTEGER NOT NULL, product_id TEXT NOT NULL,
                    quantity REAL NOT NULL, unit_price REAL NOT NULL, discount_pct REAL DEFAULT 0, tax_rate REAL DEFAULT 0,
                    line_total REAL NOT NULL, FOREIGN KEY (sale_id) REFERENCES sales(id), FOREIGN KEY (product_id) REFERENCES products(id)
                )""", "id, sale_id, product_id, quantity, unit_price, discount_pct, tax_rate, line_total"),
            ("purchase_order_items", """
                CREATE TABLE purchase_order_items_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, po_id INTEGER NOT NULL, product_id TEXT NOT NULL,
                    quantity REAL NOT NULL, unit_cost REAL NOT NULL, total REAL NOT NULL, received_qty REAL DEFAULT 0,
                    FOREIGN KEY (po_id) REFERENCES purchase_orders(id), FOREIGN KEY (product_id) REFERENCES products(id)
                )""", "id, po_id, product_id, quantity, unit_cost, total, received_qty"),
            ("return_items", """
                CREATE TABLE return_items_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, return_id INTEGER NOT NULL, product_id TEXT NOT NULL,
                    quantity REAL NOT NULL, unit_price REAL NOT NULL, line_total REAL NOT NULL,
                    FOREIGN KEY (return_id) REFERENCES returns(id), FOREIGN KEY (product_id) REFERENCES products(id)
                )""", "id, return_id, product_id, quantity, unit_price, line_total"),
        ]
        for table, create_sql, cols in referencing:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists is None:
                # `init_retail()` always creates every one of these tables
                # (via CREATE TABLE IF NOT EXISTS) before this migration ever
                # runs, so a real device is never missing one -- this guard
                # only protects a hand-built/partial database (e.g. a test
                # fixture) from an unconditional rebuild attempt against a
                # table that was never created.
                continue
            conn.execute(create_sql)
            col_list = [c.strip() for c in cols.split(",")]
            select_cols = ", ".join(c if c != "product_id" else "product_id" for c in col_list)
            conn.execute(f"INSERT INTO {table}_new ({cols}) SELECT {select_cols} FROM {table}")
            old_pids = conn.execute(f"SELECT DISTINCT product_id FROM {table} WHERE product_id IS NOT NULL").fetchall()
            for row in old_pids:
                old_pid = row["product_id"]
                new_pid = id_map.get(old_pid)
                if new_pid is not None:
                    conn.execute(
                        f"UPDATE {table}_new SET product_id=? WHERE id IN (SELECT id FROM {table} WHERE product_id=?)",
                        (new_pid, old_pid),
                    )
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")

        _restore_supplier_link(conn, legacy_supplier_links, id_map)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")


def _migrate_customers_to_uuid(conn):
    """One-time migration (still schema v4): customers.id moves from
    INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY, adds a new
    `status` column (customers never had one), and folds the
    credit_mode/credit_limit/credit_balance columns _ensure_credit_schema
    (api/retail_api.py) adds at runtime via ALTER TABLE into the rebuilt
    table as first-class columns -- _ensure_credit_schema's own addcol()
    calls stay in place as a no-op safety net (PRAGMA table_info already
    finding the column is exactly what makes addcol() skip it).

    No table has a declared FK to customers(id) -- sales.customer_id and
    payments.party_id are both loose, undeclared columns (confirmed: grep
    "REFERENCES customers" across schema.py returns nothing) -- so there is
    no SQLite auto-rewrite-on-rename hazard here. This still rebuilds under
    `customers_new` and swaps in, for consistency with every other
    migration in this file, and because relying on "no FK today" staying
    true forever is not a safe long-term assumption to bake into a
    rename-based migration.
    """
    import uuid as _uuid

    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='customers'"
    ).fetchone()
    if exists is None:
        # `init_retail()` always creates `customers` (via CREATE TABLE IF NOT
        # EXISTS) before this migration ever runs, so a real device is never
        # missing it -- this guard only protects a hand-built/partial
        # database (e.g. a test fixture that only sets up categories/
        # products) from an unconditional rebuild attempt against a table
        # that was never created. Mirrors the identical guard in
        # _migrate_products_to_uuid's referencing-table loop above.
        return

    id_col = next((c for c in conn.execute("PRAGMA table_info(customers)").fetchall() if c["name"] == "id"), None)
    if id_col is not None and id_col["type"].upper() == "TEXT":
        return

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")

        existing_cols = {c["name"] for c in conn.execute("PRAGMA table_info(customers)").fetchall()}
        has_credit = "credit_mode" in existing_cols  # _ensure_credit_schema may have already run on this db

        cust_rows = conn.execute("SELECT * FROM customers").fetchall()
        id_map = {row["id"]: str(_uuid.uuid4()) for row in cust_rows}

        conn.execute("""
            CREATE TABLE customers_new (
                id TEXT PRIMARY KEY,
                company_id INTEGER DEFAULT 1,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                address TEXT,
                loyalty_points REAL DEFAULT 0,
                total_spent REAL DEFAULT 0,
                status TEXT DEFAULT 'active',
                credit_mode TEXT DEFAULT 'none',
                credit_limit REAL DEFAULT 0,
                credit_balance REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for row in cust_rows:
            conn.execute(
                "INSERT INTO customers_new (id, company_id, name, phone, email, address, loyalty_points, "
                "total_spent, status, credit_mode, credit_limit, credit_balance, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (id_map[row["id"]], row["company_id"], row["name"], row["phone"], row["email"], row["address"],
                 row["loyalty_points"], row["total_spent"], "active",
                 row["credit_mode"] if has_credit else "none",
                 row["credit_limit"] if has_credit else 0,
                 row["credit_balance"] if has_credit else 0,
                 row["created_at"]),
            )
        conn.execute("DROP TABLE customers")
        conn.execute("ALTER TABLE customers_new RENAME TO customers")

        for row in cust_rows:
            old_id, new_id = row["id"], id_map[row["id"]]
            conn.execute("UPDATE sales SET customer_id=? WHERE customer_id=?", (new_id, old_id))
            conn.execute(
                "UPDATE payments SET party_id=? WHERE party_type='customer' AND party_id=?",
                (new_id, old_id),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")


def _migrate_suppliers_to_uuid(conn):
    """One-time migration (still schema v4): suppliers.id moves from
    INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY, folding
    payment_terms/credit_balance (added at runtime by _ensure_credit_schema)
    into the rebuilt table as first-class columns, same reasoning as
    _migrate_customers_to_uuid.

    Unlike customers, purchase_orders.supplier_id IS a declared FK
    (`FOREIGN KEY (supplier_id) REFERENCES suppliers(id)`, schema.py:428) --
    same rename-away hazard as products/categories: suppliers is rebuilt
    under `suppliers_new` and swapped in, never renamed away directly.
    """
    import uuid as _uuid

    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='suppliers'"
    ).fetchone()
    if exists is None:
        # `init_retail()` always creates `suppliers` (via CREATE TABLE IF NOT
        # EXISTS) before this migration ever runs, so a real device is never
        # missing it -- this guard only protects a hand-built/partial
        # database (e.g. a test fixture that only sets up categories/
        # products) from an unconditional rebuild attempt against a table
        # that was never created. Mirrors the identical guard in
        # _migrate_customers_to_uuid above.
        return

    id_col = next((c for c in conn.execute("PRAGMA table_info(suppliers)").fetchall() if c["name"] == "id"), None)
    if id_col is not None and id_col["type"].upper() == "TEXT":
        return

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")

        existing_cols = {c["name"] for c in conn.execute("PRAGMA table_info(suppliers)").fetchall()}
        has_credit = "payment_terms" in existing_cols

        sup_rows = conn.execute("SELECT * FROM suppliers").fetchall()
        id_map = {row["id"]: str(_uuid.uuid4()) for row in sup_rows}

        conn.execute("""
            CREATE TABLE suppliers_new (
                id TEXT PRIMARY KEY,
                company_id INTEGER DEFAULT 1,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                address TEXT,
                status TEXT DEFAULT 'active',
                payment_terms TEXT DEFAULT 'none',
                credit_balance REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for row in sup_rows:
            conn.execute(
                "INSERT INTO suppliers_new (id, company_id, name, phone, email, address, status, "
                "payment_terms, credit_balance, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (id_map[row["id"]], row["company_id"], row["name"], row["phone"], row["email"], row["address"],
                 row["status"],
                 row["payment_terms"] if has_credit else "none",
                 row["credit_balance"] if has_credit else 0,
                 row["created_at"]),
            )
        conn.execute("DROP TABLE suppliers")
        conn.execute("ALTER TABLE suppliers_new RENAME TO suppliers")

        # Merge safety (2026-08-10): a database that ran master's schema v3
        # carries master's INTEGER products.supplier_id values through this
        # chain (see _legacy_supplier_link above). They point at exactly the
        # old integer supplier ids being replaced right here, so they are
        # remapped like purchase_orders.supplier_id below. Guarded because on
        # this lineage's own upgrade path the column does not exist yet --
        # _migrate_products_add_supplier_fk adds it one step later.
        products_has_supplier_id = any(
            row["name"] == "supplier_id"
            for row in conn.execute("PRAGMA table_info(products)").fetchall()
        )

        for row in sup_rows:
            old_id, new_id = row["id"], id_map[row["id"]]
            conn.execute("UPDATE purchase_orders SET supplier_id=? WHERE supplier_id=?", (new_id, old_id))
            conn.execute(
                "UPDATE payments SET party_id=? WHERE party_type='supplier' AND party_id=?",
                (new_id, old_id),
            )
            if products_has_supplier_id:
                conn.execute("UPDATE products SET supplier_id=? WHERE supplier_id=?", (new_id, old_id))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")


def _migrate_retail_schema(conn):
    """The single `migrate_fn` handed to `ensure_schema_version` -- runs every
    migration this file owns, in version order, on any database behind
    RETAIL_SCHEMA_VERSION. `ensure_schema_version` only tells a migration
    "you are behind", not "you are behind by exactly one version", so a v1
    install upgrading straight to v7 must run ALL steps in one pass. Each
    step is independently idempotent (each inspects the live schema and
    returns immediately when its own change is already present), so running
    them all is correct regardless of which version the database actually
    starts from -- including a database that arrives stamped with master's
    now-superseded v2/v3 numbering (docs/einvoicing/phase1/,
    products.supplier_id as INTEGER; see _legacy_supplier_link/
    _restore_supplier_link above and the RETAIL_SCHEMA_VERSION comment for
    the full merge-reconciliation story, feat/retail-mobile-build-baseline,
    2026-08-10)."""
    _migrate_categories_to_uuid(conn)
    _migrate_products_category_fk_on_delete_set_null(conn)
    _migrate_products_to_uuid(conn)
    _migrate_customers_to_uuid(conn)
    _migrate_suppliers_to_uuid(conn)
    _migrate_products_add_supplier_fk(conn)
    _migrate_add_supplier_contacts_and_po_split(conn)
    # v6 -> v7 (docs/einvoicing/phase1/): adds the einvoice_* tables used by
    # opt-in Jordan JoFotara e-invoicing. CREATE TABLE IF NOT EXISTS only --
    # no existing table is ALTERed, no existing row is read or written, so an
    # install that never enables the feature gains only empty tables.
    # Appended LAST, after every migration above, so this addition cannot
    # change their order or behaviour -- exactly how
    # products/clinic/backend/database/schema.py::_apply_clinic_alters wires
    # the identical call.
    from commercial_runtime.einvoicing.schema import apply_einvoicing_schema
    apply_einvoicing_schema(conn)
    # v7 -> v8 (reorder automation foundation): appended LAST, after every
    # migration above (including einvoicing), for the identical reason
    # einvoicing itself is appended last -- see this function's own
    # docstring and the RETAIL_SCHEMA_VERSION v8 comment above.
    _migrate_add_reorder_automation_foundation(conn)
    # v8 -> v9 (feat/email-outbox-foundation): appended LAST, same reasoning
    # again -- see the RETAIL_SCHEMA_VERSION v9 comment above.
    _migrate_add_notifications_foundation(conn)
    # v9 -> v10 (feat/shift-cash-drawer): appended LAST, same reasoning again
    # -- see the RETAIL_SCHEMA_VERSION v10 comment above.
    _migrate_add_shift_cash_drawer(conn)
    # v10 -> v11 (feat/pos-hold-resume-sale): appended LAST, same reasoning
    # again -- see the RETAIL_SCHEMA_VERSION v11 comment above for the full
    # renumbering/collision story. Runs after shift-cash-drawer since both
    # branches were reconciled together at this merge.
    _migrate_add_held_sales(conn)
    # v11 -> v12 (whatsapp-recipients): appended LAST, same reasoning again --
    # see the RETAIL_SCHEMA_VERSION v12 comment above.
    _migrate_add_whatsapp_recipients(conn)
    # v12 -> v13 (launch-readiness Phase 2): appended LAST, same reasoning
    # again -- see the RETAIL_SCHEMA_VERSION v13 comment above. The first
    # step in this chain that ALTERs the sales-history tables; additive only.
    _migrate_add_identity_and_attribution_columns(conn)
    # v13 -> v14 (launch-readiness Phase 2): appended LAST, and it must stay
    # after v13 specifically -- not merely by the "new steps go last"
    # convention. The rebind rewrites `company_id` on every scoped table,
    # and running it BEFORE v13 would mean the rows it touches do not yet
    # carry the `uid` that identifies them on the wire, so an interrupted
    # rebind could not be reconciled against anything afterwards.
    _migrate_rebind_company_id_to_owner_issued(conn)
    # v14 -> v15 (launch-readiness Phase 3): appended LAST, and it must stay
    # last for a reason of its own on top of the convention. This step is a
    # GATE -- it recomputes drift after seeding and raises rather than let
    # `ensure_schema_version` advance the marker on an install whose ledger
    # cannot reproduce its balances. Anything appended after it would be
    # silently skipped on exactly those installs. It must also stay after
    # v14: the rebind rewrites `company_id` on every scoped table, and this
    # step groups, gates and reports per company, so running it first would
    # measure and record the tenant key the shop is about to stop using.
    _migrate_seed_opening_counts_and_gate_drift(conn)


def _migrate_products_add_supplier_fk(conn):
    """One-time migration (schema v4 -> v5): adds products.supplier_id.

    TEXT, not INTEGER -- suppliers.id is already UUID text by the time this
    runs (_migrate_suppliers_to_uuid, just above, runs first). This is a
    brand-new nullable column, not a change to an existing constraint, so
    unlike _migrate_products_category_fk_on_delete_set_null above, no
    DROP+RENAME rebuild is needed: SQLite allows ADD COLUMN with a
    REFERENCES clause directly, and NULL always satisfies a foreign key
    check, so existing rows are left unassigned rather than backfilled.

    Idempotent: skips the ALTER if the column already exists. Index creation
    is deliberately OUTSIDE that guard: an index lives and dies with its
    table, so every products rebuild earlier in this chain drops
    idx_products_supplier along with the old table. Returning early on
    "column already exists" (as this did before the merge-safety fix in
    _legacy_supplier_link/_restore_supplier_link above) would leave a
    database that arrived carrying master's v3 supplier_id with the column
    present but no index. IF NOT EXISTS makes the normal path a no-op.
    """
    cols = {row[1] for row in conn.execute('PRAGMA table_info(products)').fetchall()}
    if 'supplier_id' not in cols:
        conn.execute('ALTER TABLE products ADD COLUMN supplier_id TEXT REFERENCES suppliers(id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_products_supplier ON products(supplier_id)')


def _migrate_add_supplier_contacts_and_po_split(conn):
    """One-time migration (schema v5 -> v6): PO-preview-by-supplier
    foundation (Thursday demo, Stream B -- descoped to preview-only PO
    splitting by supplier; the routes that write these columns land later
    this week, not today).

    Three independent additive pieces, each guarded by its own idempotency
    check (mirrors _migrate_products_add_supplier_fk just above -- brand-new
    nullable columns / IF NOT EXISTS tables, so unlike the UUID migrations
    earlier in this chain, no DROP+RENAME rebuild is needed anywhere here):

    1. `supplier_contacts` -- a supplier can have several named contacts
       (orders/accounts/general), each with its own preferred channel. New
       table entirely, so CREATE TABLE IF NOT EXISTS is itself idempotent.

    2. `purchase_orders` gains split-group/routing/idempotency columns:
       split_group_id/split_index/split_count identify which slice of a
       supplier-split preview a PO belongs to; routing_status/routed_channel/
       routed_at/routed_to record how (and whether) it was sent once routing
       ships; idempotency_key gets the same partial-unique-index treatment as
       `returns.idempotency_key` (see api/retail_api.py's
       `_ensure_credit_schema`, `idx_returns_idempotency`) -- SQLite can't add
       a UNIQUE column via ALTER TABLE, so a partial unique index does the
       job instead, and NULLs (every pre-v6 row) are exempt, matching
       SQLite's own UNIQUE-column NULL semantics.

    3. `suppliers.min_order_value` -- procurement metadata the split
       preview's MOQ warning reads; not touched by anything else yet.

    Idempotent: each ALTER COLUMN is preceded by a PRAGMA table_info check,
    each CREATE TABLE/INDEX already uses IF NOT EXISTS, so a second call
    (or a fresh install that already has this shape via init_retail's base
    executescript) is a clean no-op.
    """
    existing_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    if 'supplier_contacts' not in existing_tables:
        conn.execute("""
            CREATE TABLE supplier_contacts (
                id TEXT PRIMARY KEY,
                company_id TEXT,
                supplier_id TEXT NOT NULL REFERENCES suppliers(id),
                name TEXT NOT NULL,
                role TEXT DEFAULT 'orders',
                email TEXT,
                phone TEXT,
                whatsapp TEXT,
                channel_preference TEXT DEFAULT 'whatsapp',
                is_primary INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_supplier_contacts_supplier "
        "ON supplier_contacts(company_id, supplier_id, status)"
    )

    if 'purchase_orders' in existing_tables:
        po_cols = {row[1] for row in conn.execute('PRAGMA table_info(purchase_orders)').fetchall()}
        for col, decl in [
            ('split_group_id', 'TEXT'),
            ('split_index', 'INTEGER'),
            ('split_count', 'INTEGER'),
            ('routing_status', 'TEXT'),
            ('routed_channel', 'TEXT'),
            ('routed_at', 'TEXT'),
            ('routed_to', 'TEXT'),
            ('idempotency_key', 'TEXT'),
        ]:
            if col not in po_cols:
                conn.execute(f'ALTER TABLE purchase_orders ADD COLUMN {col} {decl}')
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_po_split_group "
            "ON purchase_orders(company_id, split_group_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_po_idempotency "
            "ON purchase_orders(idempotency_key) WHERE idempotency_key IS NOT NULL"
        )

    if 'suppliers' in existing_tables:
        sup_cols = {row[1] for row in conn.execute('PRAGMA table_info(suppliers)').fetchall()}
        if 'min_order_value' not in sup_cols:
            conn.execute('ALTER TABLE suppliers ADD COLUMN min_order_value REAL DEFAULT 0')


def _migrate_add_reorder_automation_foundation(conn):
    """One-time migration (schema v7 -> v8): reorder automation foundation
    (feat/reorder-automation-foundation) -- the SAFE subset of a proposed
    "low-stock auto-reorder via WhatsApp" feature: schema + the post-sale
    trigger + an Admin Center accept/decline page. The WhatsApp send itself
    is explicitly deferred (out of scope for this migration and everything
    it enables).

    Two independent additive pieces, each guarded by its own idempotency
    check (mirrors _migrate_add_supplier_contacts_and_po_split immediately
    above -- brand-new nullable column / IF NOT EXISTS table, so no
    DROP+RENAME rebuild is needed here either):

    1. `products.reorder_method` -- per-product opt-in ('none' by default,
       so an install that never touches this column gets zero behavior
       change from the post-sale hook that reads it). Deliberately reuses
       the EXISTING `products.supplier_id` column (schema v5) for "which
       supplier to draft the reorder against" rather than adding a new
       `default_supplier_id` -- that would have been a redundant column
       duplicating data v5 already carries.

    2. `reorder_requests` -- one row per auto-detected low-stock event
       needing human accept/decline. `id` is a client-generated UUID, NOT
       autoincrement -- deliberately, so this table CAN be relayed through
       Owner's sync (commercial_runtime/sync/sync_service.py's
       `_apply_event` gains a matching `reorder_request` branch). This is
       the opposite choice from `purchase_orders` (still
       INTEGER PRIMARY KEY AUTOINCREMENT): Owner's relay
       (owner/app/sync/routes.py) requires `entity_id` to parse as a UUID
       and 400-rejects the WHOLE PUSH BATCH on the first entity_id that
       doesn't -- syncing purchase_orders directly would permanently jam
       every other entity's sync behind one malformed event the instant a
       PO existed. reorder_requests never has that problem because it was
       never given an autoincrement id in the first place. The PO an Accept
       action creates, by contrast, IS a real purchase_orders row and is
       therefore deliberately never queued to sync_outbox at all -- see
       api/retail_api.py's accept route.

       `status` is 'pending' | 'accepted' | 'declined'. The partial unique
       index below (idx_reorder_requests_open) enforces the idempotency
       invariant the post-sale hook relies on -- at most one OPEN
       ('pending' or 'accepted') request per (company_id, product_id) at a
       time -- as a real database constraint, not just an application-level
       check, mirroring purchase_orders.idempotency_key's partial-unique-
       index treatment (_migrate_add_supplier_contacts_and_po_split above).
       A 'declined' request does NOT hold this slot open, so a later sale
       dropping the same product below its reorder_level again is free to
       open a new request.

    Idempotent: the ALTER COLUMN is preceded by a PRAGMA table_info check,
    the CREATE TABLE/INDEX statements already use IF NOT EXISTS, so a
    second call (or a fresh install migrating 0 -> 8 in one pass, same as
    every step above) is a clean no-op.
    """
    cols = {row[1] for row in conn.execute('PRAGMA table_info(products)').fetchall()}
    if 'reorder_method' not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN reorder_method TEXT DEFAULT 'none'")

    existing_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if 'reorder_requests' not in existing_tables:
        conn.execute("""
            CREATE TABLE reorder_requests (
                id TEXT PRIMARY KEY,
                company_id TEXT,
                branch_id INTEGER,
                product_id TEXT NOT NULL REFERENCES products(id),
                status TEXT NOT NULL DEFAULT 'pending',
                draft_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT
            )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_reorder_requests_company "
        "ON reorder_requests(company_id, status)"
    )
    # Idempotency guard for the post-sale hook (retail_api.py::create_sale
    # via core/retail/reorder_hook.py): at most one pending OR accepted
    # request per product at a time. Declined rows are exempt (status not
    # in the predicate), same NULL/exempt-row shape as idx_po_idempotency.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_reorder_requests_open "
        "ON reorder_requests(company_id, product_id) WHERE status IN ('pending','accepted')"
    )


def _migrate_add_notifications_foundation(conn):
    """One-time migration (schema v8 -> v9): email outbox foundation
    (feat/email-outbox-foundation) -- adds the email_* tables owned by
    commercial_runtime/notifications (settings, outbox, verification
    tokens). CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS only,
    identical shape to v7's einvoice_* addition above -- see
    commercial_runtime/notifications/schema.py's own docstring for the full
    table-by-table reasoning; nothing product-specific lives in that
    module, so this function's only job is to call it inside the same
    ensure_schema_version() transaction every other step in this chain
    already runs inside.

    Idempotent by construction (every statement inside
    apply_notifications_schema() is already IF NOT EXISTS), so a second
    call -- or a fresh install migrating 0 -> 9 in one pass, same as every
    step above -- is a clean no-op.
    """
    from commercial_runtime.notifications.schema import apply_notifications_schema
    apply_notifications_schema(conn)


def _migrate_add_shift_cash_drawer(conn):
    """One-time migration (schema v9 -> v10): shift / cash-drawer management
    (feat/shift-cash-drawer) -- cash float in/out tracking plus X (mid-shift,
    non-destructive) and Z (end-of-shift, locking) reports.

    Three independent additive pieces, each guarded by its own idempotency
    check (same shape as _migrate_add_reorder_automation_foundation above --
    brand-new nullable columns / IF NOT EXISTS tables, so no DROP+RENAME
    rebuild is needed here either):

    1. `cash_sessions` -- one row per opened-to-closed till session. `id` is a
       client-generated UUID, NOT autoincrement, matching reorder_requests'
       reasoning exactly (see this file's own RETAIL_SCHEMA_VERSION v8
       comment): a UUID id means this table COULD be relayed through Owner's
       sync later without hitting the purchase_orders-style "entity_id must
       parse as a UUID" wall, even though it isn't wired into sync_outbox
       yet. idx_cash_sessions_one_open_per_branch is a partial UNIQUE index
       enforcing "at most one OPEN session per (company_id, branch_id)" as a
       real database constraint, not just an application-level check --
       identical technique to idx_reorder_requests_open.

    2. `cash_movements` -- one row per float-in/float-out/paid-in/paid-out
       event against a session. Deliberately has NO company_id/branch_id of
       its own: a movement has no meaning outside the session it belongs to,
       so `session_id` is the only scope it needs (same shape as sale_items
       scoping entirely through sale_id rather than repeating company_id).

    3. `sales.session_id` / `returns.session_id` -- nullable TEXT columns
       referencing cash_sessions(id), stamped at write time by
       retail_api.py's `_open_cash_session_id()` helper so the X/Z report
       math can attribute a sale/return to the EXACT session it happened in,
       via a direct FK rather than a branch+time-range lookup -- see the
       RETAIL_SCHEMA_VERSION v10 comment above for why a time-range lookup
       is the wrong choice here (breaks across midnight / back-to-back
       sessions). NULL for every row written before this migration, and for
       every row written by an install that never opens a cash session --
       zero behavior change to create_sale/create_return in that case (see
       retail_cash_drawer_regression_test.py).

    Idempotent: each ALTER COLUMN is preceded by a PRAGMA table_info check,
    each CREATE TABLE/INDEX already uses IF NOT EXISTS, so a second call (or
    a fresh install migrating 0 -> 10 in one pass, same as every step above)
    is a clean no-op.
    """
    existing_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    if 'cash_sessions' not in existing_tables:
        conn.execute("""
            CREATE TABLE cash_sessions (
                id TEXT PRIMARY KEY,
                company_id TEXT,
                branch_id INTEGER NOT NULL,
                opened_by TEXT,
                opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                opening_float REAL NOT NULL DEFAULT 0,
                closed_by TEXT,
                closed_at TIMESTAMP,
                closing_float_counted REAL,
                closing_float_expected REAL,
                variance REAL,
                status TEXT NOT NULL DEFAULT 'open'
            )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cash_sessions_company "
        "ON cash_sessions(company_id, branch_id, status)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_cash_sessions_one_open_per_branch "
        "ON cash_sessions(company_id, branch_id) WHERE status='open'"
    )

    if 'cash_movements' not in existing_tables:
        conn.execute("""
            CREATE TABLE cash_movements (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES cash_sessions(id),
                type TEXT NOT NULL,
                amount REAL NOT NULL,
                reason TEXT,
                created_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cash_movements_session ON cash_movements(session_id)"
    )

    # `sales`/`returns` are guarded by existence the same way
    # _migrate_add_supplier_contacts_and_po_split guards `purchase_orders`
    # above -- every real install has both (init_retail's own executescript
    # creates them), but some test fixtures hand-build a MINIMAL schema
    # (e.g. retail_category_delete_fk_sync_test.py's v1/v2 fixtures, which
    # only contain categories/products/inventory_movements) and call
    # `_migrate_retail_schema` directly against it. An unconditional ALTER
    # here would crash that fixture with "no such table: sales" even though
    # this migration has nothing to do with categories/products at all.
    # `existing_tables` (captured before cash_sessions/cash_movements were
    # created above) is still accurate for sales/returns -- neither of those
    # CREATE TABLE calls could have created or dropped sales/returns.
    if 'sales' in existing_tables:
        sales_cols = {row[1] for row in conn.execute('PRAGMA table_info(sales)').fetchall()}
        if 'session_id' not in sales_cols:
            conn.execute('ALTER TABLE sales ADD COLUMN session_id TEXT REFERENCES cash_sessions(id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_sales_session ON sales(session_id)')

    if 'returns' in existing_tables:
        returns_cols = {row[1] for row in conn.execute('PRAGMA table_info(returns)').fetchall()}
        if 'session_id' not in returns_cols:
            conn.execute('ALTER TABLE returns ADD COLUMN session_id TEXT REFERENCES cash_sessions(id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_returns_session ON returns(session_id)')


def _migrate_add_held_sales(conn):
    """One-time migration (schema v9 -> v11, deliberately skipping v10 --
    see RETAIL_SCHEMA_VERSION's comment for the full collision story with
    feat/shift-cash-drawer, which claimed v10 on the same base): hold/resume
    sale ("park a cart") on the POS screen (feat/pos-hold-resume-sale). Adds
    the held_sales table. CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT
    EXISTS only -- no existing table is ALTERed, read, or written, identical
    shape to v7/v8/v9's additive migrations above.

    A held sale is PRE-completion cart state -- a cashier's in-progress
    draft, never a commercial transaction -- so this is deliberately its own
    table, never a row in `sales`/`sale_items`. `subtotal`/`total` here are
    DISPLAY-ONLY (rendered in the resume picker) -- they are never read by
    create_sale/POST /sales, which always recomputes the real, authoritative
    figures from live product data the same way it does for any freshly-
    built cart (AUDIT-002/AUDIT-003's server-authority guarantee is
    unchanged; this table sits entirely outside that financial-authority
    boundary). `cart_json` carries the full line-item snapshot
    (product_id/quantity/unit_price/tax_rate/line_total/max_stock, mirroring
    RetailSystem._cart's own shape) plus discount_pct/payment_method/
    customer_id so a resume can repopulate the POS screen exactly as it was
    left.

    `customer_id` is TEXT, not INTEGER -- unlike the original commit this
    migration was renumbered from (schema v3 -> v4 on an old base where
    customers.id was still a plain autoincrement integer), customers.id is
    UUID TEXT on this lineage as of _migrate_customers_to_uuid above, so the
    declared column type is fixed here to match the real shape of the value
    it stores. `branch_id` stays INTEGER -- branches.id was never part of
    the UUID migration (see _migrate_categories_to_uuid/_migrate_products_
    to_uuid/_migrate_customers_to_uuid/_migrate_suppliers_to_uuid above;
    branches was never in that list).

    Local-only, NOT part of commercial_runtime/sync/'s outbox -- a mid-edit
    cart isn't a natural sync entity (two devices don't need to see each
    other's in-progress carts, and syncing this table would require solving
    conflict resolution this feature doesn't need to take on); explicit
    scope decision, not an oversight.

    Idempotent: CREATE TABLE/INDEX already use IF NOT EXISTS, so a second
    call -- or a fresh install migrating 0 -> 11 in one pass, same as every
    step above -- is a clean no-op.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS held_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER DEFAULT 1,
            branch_id INTEGER,
            customer_id TEXT,
            hold_number TEXT,
            label TEXT DEFAULT '',
            cart_json TEXT NOT NULL,
            item_count INTEGER DEFAULT 0,
            subtotal REAL DEFAULT 0,
            total REAL DEFAULT 0,
            held_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id),
            FOREIGN KEY (branch_id) REFERENCES branches(id)
        );
        CREATE INDEX IF NOT EXISTS idx_held_sales_company ON held_sales(company_id, created_at);
    """)


def _migrate_add_whatsapp_recipients(conn):
    """One-time migration (schema v11 -> v12): adds the whatsapp_recipients
    table owned by commercial_runtime/notifications (multi-recipient,
    role/branch-scoped WhatsApp report routing) -- identical shape to v9's
    email/whatsapp-outbox addition above: CREATE TABLE IF NOT EXISTS /
    CREATE INDEX IF NOT EXISTS only, nothing product-specific lives in that
    module, so this function's only job is to call it inside the same
    ensure_schema_version() transaction every other step in this chain
    already runs inside.

    Idempotent by construction (every statement inside
    apply_whatsapp_recipients_schema() is already IF NOT EXISTS), so a
    second call -- or a fresh install migrating 0 -> 12 in one pass, same as
    every step above -- is a clean no-op.
    """
    from commercial_runtime.notifications.schema import apply_whatsapp_recipients_schema
    apply_whatsapp_recipients_schema(conn)


def local_terminal_id():
    """This device's terminal id, or None when it has never been established.

    Deliberately NOT a new notion of device identity. It returns
    `commercial_runtime.identity.device_context.peek_local_device_uuid()` --
    the install-stable UUID persisted in
    <AURA_APP_DATA>/device/local_device.json, which `device_registry.devices`
    is already keyed by. Inventing a second "which terminal is this" value
    here would guarantee that `sales.terminal_id` and `devices.id` disagree
    the first time anyone tried to join them.

    `peek_*` rather than `local_device_uuid()` on purpose: the peek variant
    never CREATES the identity file. Stamping a row is a bookkeeping question,
    not a reason to manufacture an install identity as a side effect -- the
    same reasoning `local_device_is_admin()` uses for choosing the peek
    variant (see that function's docstring).

    Returns None instead of raising on LocalDeviceStateCorruptError: a
    corrupt local-device record is a real problem, but the correct place to
    surface it is device resolution, not the middle of a sale. An
    unattributed row is recoverable; a refused sale is not.
    """
    try:
        from commercial_runtime.identity.device_context import (
            LocalDeviceStateCorruptError,
            peek_local_device_uuid,
        )
        return peek_local_device_uuid()
    except Exception:
        # Includes LocalDeviceStateCorruptError and, on a stripped Android
        # build, an ImportError. Both mean "no trustworthy terminal id".
        return None


def _uid_index_shape(conn, table, name):
    """`{'unique': int, 'partial': int, 'columns': [...]}` for the index called
    `name` on `table`, or None if there is no such index.

    Read from `PRAGMA index_list`, which reports the shape SQLite actually
    stored, rather than from `sqlite_master.sql`, which reports the text
    somebody typed. The distinction is the entire point of F4: a
    `CREATE UNIQUE INDEX IF NOT EXISTS` whose name is already taken by a
    plain index leaves the plain index in place and the UNIQUE text nowhere.

    `columns` comes from a second pragma because `index_list` does not carry
    it, and the COLUMN is part of the shape every bit as much as uniqueness
    is. An index named `idx_<t>_uid` that is genuinely UNIQUE and genuinely
    partial but built over `id` satisfies a unique-and-partial check
    completely, while `uid` uniqueness stays absent and `user_version`
    advances anyway -- F4's own consequence, reached past F4's own fix. Not
    hypothetical here: CLAUDE.md warns that databases in this project get
    touched by more than one branch's schema version during testing, and this
    migration re-runs from the top after any failure.
    """
    for _seq, iname, unique, _origin, partial in conn.execute(
        f'PRAGMA index_list("{table}")'
    ).fetchall():
        if iname == name:
            return {
                'unique': unique,
                'partial': partial,
                'columns': [row[2] for row in conn.execute(
                    f'PRAGMA index_info("{name}")'
                ).fetchall()],
            }
    return None


def _uid_index_is_correct(shape):
    """True when `shape` (from `_uid_index_shape`) is the ONE shape that makes
    `uid` mean anything: a UNIQUE, partial index over exactly `uid`.

    A single predicate rather than the condition written out twice, because
    `_ensure_unique_uid_index` asks the question twice -- once to decide
    whether to rebuild, once to verify the postcondition -- and the two must
    not be able to drift apart. If the rebuild test were ever stricter than
    the verify test, the migration would drop and recreate an index on every
    launch forever; if the verify test were stricter, it would rebuild and
    then raise on its own work. Both failures are launch-path failures, and
    neither is visible in a diff that only touches one of the two lines.
    """
    return bool(
        shape is not None
        and shape['unique']
        and shape['partial']
        and shape['columns'] == ['uid']
    )


def _repair_duplicate_uids(conn, table):
    """Reissue `uid` on every row that shares one with an earlier row, keeping
    the lowest rowid's value. Returns how many rows were reissued.

    WHY REPAIR AND NOT RAISE. `CREATE UNIQUE INDEX` on a column holding a
    duplicate raises sqlite3.IntegrityError, and there is nowhere for that
    exception to go that is not fatal: `app.py::init_app()` calls
    `init_retail()` unconditionally, and `ensure_schema_version` advances
    `user_version` only on full success. So one duplicate uid did not fail
    one migration -- it stopped the app from ever starting again AND froze
    the version marker, blocking every future migration behind a condition
    the customer has no way to clear. A till that will not open is not an
    acceptable response to a bookkeeping collision.

    WHY REPAIR IS SAFE HERE, and would not be on most columns: `uid` is a
    WIRE identity minted by this very migration. It is not something a human
    typed, it encodes no business meaning, and at v13 nothing outside this
    database references it yet. Regenerating one loses nothing. (Contrast
    `cashier`/`created_by`, which this migration will not touch under any
    circumstances -- a wrong name on a sale is worse than no name.)

    The LOWEST rowid keeps its value on purpose. If part of this table has
    already been shared with a peer device or Owner, the original row is the
    one likelier to have been seen under that uid; the interloper -- a
    restored row, a merged row, a row copied by support SQL -- is the one
    that should move.

    No shipped writer produces a duplicate today: every one of them uses
    `uuid.uuid4()`, and the `sale_items` uid is correctly generated inside
    the per-line loop rather than once outside it. This exists for the paths
    that are not shipped writers -- a restore, a two-database merge, a
    hand-written support fix, or any future row-copy -- which are exactly the
    moments a customer can least afford "the app will not open".

    Chunked and re-queried each pass for the same reason the backfill above
    is: `sale_items` on a shop with years of history is the biggest table in
    this file by an order of magnitude, this runs on a till machine, and a
    loop that re-reads its own work terminates correctly even if it is
    interrupted and retried.

    THE PREDICATE HAS TO SHRINK ITS OWN WORK QUEUE, and that is the entire
    correctness argument for a loop shaped like this one. A row is selected
    only when a row with a LOWER rowid already holds its uid; the fresh uuid4
    written into it therefore makes it the lowest -- and only -- holder of a
    value nothing else has, so it cannot be selected again. Each pass strictly
    reduces the number of matching rows, and the loop ends.

    That is not a theoretical concern. The first version of this function
    matched on `... > 1 OR (SELECT COUNT(*) ...) = 1`, and SQL's `AND` binding
    tighter than `OR` made that second disjunct a whole-table predicate: every
    row with a distinct uid matched it, was reissued, and matched it again on
    the next pass. The loop never emptied. That is worse than the F1 defect
    this function was written to fix -- F1 raised IntegrityError, which is
    fatal but names its own cause, whereas a spinning repair makes
    `init_retail()` never return at all: a till stuck on a splash screen with
    no error, no log line and no traceback, rewriting every uid in the database
    several times a second, reached again on every subsequent launch because
    the ADD COLUMN above it has already committed.
    `retail_v13_uid_repair_termination_test.py` pins termination and bounded
    work directly, by counting write statements, because a non-terminating loop
    does not fail a test suite -- it hangs one, and `products/run_all_tests.py`
    runs each file with no subprocess timeout at all.

    The `uid IN (SELECT ... HAVING COUNT(*) > 1)` pre-filter is not redundant
    with the `rowid >` test that follows it; it is what keeps this affordable.
    That subquery is uncorrelated, so SQLite evaluates it once per pass into a
    set that is EMPTY on every shipped install, and the correlated MIN(rowid)
    lookup then runs for no rows at all. Without it, the correlated subquery
    would run once per row against a `uid` column that has no index yet (this
    function runs precisely to make creating that index possible), which is a
    full table scan per row -- quadratic on the largest table in the schema, on
    a shop laptop, during boot.
    """
    import uuid as _uuid

    reissued = 0
    while True:
        pending = conn.execute(
            f'SELECT rowid FROM "{table}" AS t'
            f' WHERE t.uid IS NOT NULL'
            f'   AND t.uid IN (SELECT uid FROM "{table}" WHERE uid IS NOT NULL'
            f'                 GROUP BY uid HAVING COUNT(*) > 1)'
            f'   AND t.rowid > (SELECT MIN(x.rowid) FROM "{table}" x WHERE x.uid = t.uid)'
            f' LIMIT {_UID_BACKFILL_CHUNK}'
        ).fetchall()
        if not pending:
            return reissued
        conn.executemany(
            f'UPDATE "{table}" SET uid=? WHERE rowid=?',
            [(str(_uuid.uuid4()), row[0]) for row in pending],
        )
        reissued += len(pending)


def _ensure_unique_uid_index(conn, table):
    """Leave `idx_<table>_uid` existing, UNIQUE and partial -- verified, not
    assumed. Fixes the two ways the original one-liner failed.

        conn.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_uid '
                     f'ON "{table}"(uid) WHERE uid IS NOT NULL')

    F1: unguarded. A duplicate uid raised IntegrityError straight out of
    `init_retail()` -- see `_repair_duplicate_uids` for why that is permanent
    rather than merely annoying. Duplicates are now found and repaired BEFORE
    the index is created, so the statement cannot fail for that reason.

    F4: unverified. `IF NOT EXISTS` is a statement about the NAME, never
    about the SHAPE. A same-named NON-unique index -- left by an interrupted
    earlier attempt of this very migration, or by a support fix -- satisfied
    it completely, so v13 reported success, `user_version` advanced to 14,
    and uid uniqueness was absent forever with no signal anywhere. That is
    the worse of the two failures: F1 at least announces itself.

    ALL THREE PARTS OF THE SHAPE ARE CHECKED, not just uniqueness, and they
    are not all load-bearing for the same reason:

      - COLUMN is the one that silently defeats the whole migration. An index
        of this name that is UNIQUE and partial but built over `id` passes a
        uniqueness check completely and enforces nothing whatsoever about
        `uid`. That is F4's own consequence -- success reported, version
        advanced, constraint absent -- reached past F4's own fix.
      - UNIQUE is F4 as originally found.
      - PARTIAL is NOT a correctness requirement, and it is worth being
        precise about that rather than repeating a plausible-sounding reason.
        A full UNIQUE index over `uid` would NOT reject the un-backfilled
        rows: SQLite considers every NULL distinct from every other NULL for
        the purposes of a unique index, so any number of NULL uids coexist
        under either shape. What the predicate buys is that the index carries
        no entry at all for rows that have no wire identity yet. It is checked
        here because it is the shape this migration DECLARES, and an index
        that does not match what this migration declares was put there by
        something else -- which is precisely the situation worth rebuilding
        out of, whatever the divergence happens to be.

    A mis-shaped index is DROPPED and recreated. That is the one piece of
    non-additive DDL in this migration and it is deliberate: dropping an
    index destroys no row and no column, only a derived structure this
    migration owns and is about to rebuild correctly. The behavioural
    additive-only guard (`retail_v13_additive_only_behavioural_test.py`)
    compares every row of every table across this migration and is
    untroubled by it, which is the check that actually matters.

    The postcondition is then re-read and RAISED on, not logged. See
    RetailUidIndexError: after the repair above there is no remaining
    mechanism for it to fire, and if one is ever found, stopping is right --
    the alternative is advancing the version marker on a database where the
    wire identity is not unique, which is F4 again by a different route.
    """
    name = f'idx_{table}_uid'

    _repair_duplicate_uids(conn, table)

    shape = _uid_index_shape(conn, table, name)
    if shape is not None and not _uid_index_is_correct(shape):
        conn.execute(f'DROP INDEX "{name}"')
        shape = None
    if shape is None:
        conn.execute(
            f'CREATE UNIQUE INDEX "{name}" ON "{table}"(uid) WHERE uid IS NOT NULL'
        )

    shape = _uid_index_shape(conn, table, name)
    if not _uid_index_is_correct(shape):
        raise RetailUidIndexError(
            f'{name} could not be established as a UNIQUE partial index on '
            f'{table}.uid (observed: {shape!r}). user_version is NOT being advanced, '
            f'so this retries on the next launch rather than shipping a wire identity '
            f'that is not unique.'
        )


def _migrate_add_identity_and_attribution_columns(conn):
    """One-time migration (schema v13): identity and attribution columns --
    launch-readiness Phase 2, reserved in ROADMAP.md's 2026-08-21 ledger.

    This is the first migration in this file to ALTER the tables that hold a
    real shop's entire sales history, so it is additive ONLY -- every
    statement below is `ALTER TABLE ... ADD COLUMN`,
    `CREATE [UNIQUE] INDEX IF NOT EXISTS`, or an `UPDATE` guarded on the rows
    that still need it. There is no table rebuild here, and there must never
    be one: the rebuild technique `_migrate_products_to_uuid` uses copies
    every row through a replacement table with `PRAGMA foreign_keys`
    temporarily OFF, and a mistake in one column list loses real financial
    data with no exception and a passing integrity_check (this lineage has
    already lived through exactly that -- see `_legacy_supplier_link`'s
    docstring, where every product's supplier link silently came back NULL).
    `retail_v13_additive_only_behavioural_test.py` pins that constraint
    behaviourally -- it snapshots every row of every table, values included,
    and asserts nothing outside the additive change below differs afterwards.
    (`retail_v13_identity_columns_migration_test.py` also keeps a cheap
    source-level tripwire, but that one is a convenience, not the guarantee:
    it cannot see DML data destruction, nor a statement assembled from a
    variable, and four such mutations shipped past it green.)

    THREE GROUPS OF COLUMNS:

    1. `uid` on RETAIL_UID_TABLES -- the WIRE identity, exactly what registry
       v3 gave `users` (commercial_runtime/identity/account_schema.py). The
       existing autoincrement `id` stays the primary key and stays a private
       detail of THIS install; `uid` is what a peer device, or Owner's relay,
       names the row by. Backfilled with one fresh `uuid.uuid4()` PER ROW,
       generated in Python.

       Generated in Python, and never as `lower(hex(randomblob(16)))`: that
       SQL trick produces a 32-character string which is NOT RFC-4122, and
       Owner's sync ingest gates on `uuid.UUID(entity_id)`. A value that
       merely looks random passes every local eye test and is then rejected
       on the wire, at the point where it is most expensive to discover.
       The registry v3 migration hit precisely this and documents it; the
       same decision is repeated here rather than re-learned.

       `ALTER TABLE ADD COLUMN` cannot carry a UNIQUE constraint (and adding
       one the "proper" way means the table rebuild this migration exists to
       avoid), so uniqueness is a separate partial unique index. The
       `WHERE uid IS NOT NULL` predicate keeps the index from carrying an
       entry for rows that have no wire identity yet -- between this ALTER
       and the backfill, and for any row an older code path inserts before
       the writers learn about the column, "no uid yet" is a legal state.
       It is NOT what makes those rows legal, and the difference is worth
       stating because the opposite is the intuitive guess: SQLite treats
       every NULL as distinct from every other NULL inside a unique index, so
       un-backfilled rows would coexist happily under a full index too.

    2. `actor_user_uid` / `terminal_id` / `created_at_utc` on
       RETAIL_ACTOR_TABLES. The existing free-text `cashier` / `created_by` /
       `opened_by` columns are KEPT and never read, rewritten, or used to
       guess at `actor_user_uid`. A wrong name on a sale is worse than no
       name: the free text is the only surviving evidence of who the shop
       believed rang a transaction, and a fuzzy name match that guessed wrong
       would destroy that evidence while looking like an improvement.

       `created_at_utc` is the one that matters most and looks least
       important. Today's reports bucket on `date(created_at)` and
       `strftime(...)` over a value that is NOT consistently UTC: `create_sale`
       and `create_return` (api/retail_api.py) write LOCAL wall clock
       deliberately -- there is a comment there explaining that the dashboard
       nets returns out of today's revenue by local date -- while
       `inventory_movements` takes SQLite's DEFAULT CURRENT_TIMESTAMP, which
       IS UTC. So the same column name already means two different things in
       two tables, and two devices in two timezones (or one device with a
       wrong clock) file rows on the wrong day, quietly, with every daily
       total wrong and nothing to show for it.

       Existing rows are left NULL rather than converted. The only offset
       available at migration time is THIS machine's CURRENT one, which is
       wrong by an hour for half of every year and wrong by whole hours for a
       row rung on a device somewhere else -- a fabricated instant, which is
       the same category of mistake as a fabricated cashier name. THE
       CONSEQUENCE IS LOAD-BEARING for whoever moves the report predicates
       onto this column: they must read `COALESCE(created_at_utc, created_at)`,
       never `created_at_utc` alone, or every historical row silently drops
       out of every report. `terminal_id` and `actor_user_uid` are left NULL
       on history for the identical reason -- this device cannot prove it is
       the terminal that rang a sale from before the column existed.
       `local_terminal_id()` above is the source write-time code should use.

    3. `row_version` / `updated_at_utc` / `deleted_at_utc` on
       RETAIL_ROW_VERSION_TABLES -- the reject-stale marker and soft
       tombstone, matching the identical triple registry v3 put on `users`.
       `row_version INTEGER NOT NULL DEFAULT 1` is only legal on ADD COLUMN
       because the non-null DEFAULT is supplied, which SQLite writes into
       every existing row in place. `deleted_at_utc` stays NULL everywhere:
       nobody has been deleted yet, and a tombstone stamped by a migration
       would be a lie about when.

    Idempotent: every ADD COLUMN is preceded by a `PRAGMA table_info` check,
    every index uses IF NOT EXISTS, and the uid backfill only touches rows
    that have no uid -- so a retried run (the normal case, since
    `ensure_schema_version` leaves `user_version` un-advanced on any failure
    and the whole chain re-runs on the next launch) is a clean no-op.

    Every table is existence-guarded first. Some test fixtures hand-build a
    MINIMAL schema and call `_migrate_retail_schema` against it directly
    (retail_category_delete_fk_sync_test.py builds only categories/products/
    inventory_movements), and an unconditional `ALTER TABLE sales` would
    crash them with "no such table: sales" -- the same hazard
    `_migrate_add_shift_cash_drawer` already guards for.
    """
    import uuid as _uuid

    live_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    def _columns(table):
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}

    # ── group 1: the wire identity ──────────────────────────────────────
    for table in RETAIL_UID_TABLES:
        if table not in live_tables:
            continue
        if 'uid' not in _columns(table):
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN uid TEXT')
        # Backfilled by rowid, not by `id`: rowid exists on every one of
        # these tables regardless of what their primary key is declared as,
        # so this keeps working if one of them is ever given a TEXT id the
        # way products/customers/suppliers already were.
        #
        # Chunked, not one giant executemany. `sale_items` on a shop with a
        # few years of history is the largest table in this database by an
        # order of magnitude, and materialising every rowid AND every
        # generated uuid as Python objects at once is hundreds of megabytes
        # on a machine that is also running the app. The chunk loop re-runs
        # the same "still needs a uid" query each pass, so it terminates on
        # its own and stays correct if it is interrupted and retried.
        while True:
            pending = conn.execute(
                f'SELECT rowid FROM "{table}" '
                f"WHERE uid IS NULL OR TRIM(uid) = '' LIMIT {_UID_BACKFILL_CHUNK}"
            ).fetchall()
            if not pending:
                break
            conn.executemany(
                f'UPDATE "{table}" SET uid=? WHERE rowid=?',
                [(str(_uuid.uuid4()), row[0]) for row in pending],
            )
        _ensure_unique_uid_index(conn, table)

    # ── group 2: who / where / when-in-real-time ────────────────────────
    for table in RETAIL_ACTOR_TABLES:
        if table not in live_tables:
            continue
        cols = _columns(table)
        for column, decl in (
            ('actor_user_uid', 'TEXT'),
            ('terminal_id', 'TEXT'),
            ('created_at_utc', 'TEXT'),
        ):
            if column not in cols:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {decl}')
        # Reporting reads these by (company_id, day) far more than by row,
        # and a full scan of a year of sales on a shop laptop is the
        # difference between a report that opens and one that appears to
        # hang. Created outside the ADD COLUMN guard, IF NOT EXISTS, for the
        # same reason idx_products_supplier is (see
        # _migrate_products_add_supplier_fk): an index lives and dies with
        # its table, so an early-return on "column already exists" would
        # leave a rebuilt table indexless.
        conn.execute(
            f'CREATE INDEX IF NOT EXISTS idx_{table}_created_at_utc '
            f'ON "{table}"(created_at_utc)'
        )

    # ── group 3: reject-stale marker + soft tombstone ───────────────────
    for table in RETAIL_ROW_VERSION_TABLES:
        if table not in live_tables:
            continue
        cols = _columns(table)
        for column, decl in (
            ('row_version', 'INTEGER NOT NULL DEFAULT 1'),
            ('updated_at_utc', 'TEXT'),
            ('deleted_at_utc', 'TEXT'),
        ):
            if column not in cols:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {decl}')
        # Partial index: the overwhelmingly common query is "the live rows",
        # and a tombstoned row should cost nothing to skip. Guarded on
        # company_id the same way the whole loop is guarded on the table
        # existing -- a hand-built minimal test fixture is under no
        # obligation to carry the tenant key, and an index is not worth
        # crashing a migration over.
        if 'company_id' in _columns(table):
            conn.execute(
                f'CREATE INDEX IF NOT EXISTS idx_{table}_live '
                f'ON "{table}"(company_id) WHERE deleted_at_utc IS NULL'
            )


# ── v14: rebinding the tenant key onto the Owner-issued value ───────────────

class CompanyRebindError(Exception):
    """Raised when `rebind_company_id` cannot identify, unambiguously, which
    tenant's rows it has been asked to move. Deliberately a refusal rather
    than a best guess -- see that function's docstring."""


def company_scoped_tables(conn):
    """Every real table in this database that carries a `company_id` column.

    DISCOVERED from the live schema rather than hardcoded. A hardcoded list
    is a second place to remember, and the failure mode of forgetting is
    silent: the forgotten table keeps the old tenant key, its rows stop
    matching `retail_api._cid()`, and they simply stop appearing. Discovery
    also means every table a later migration adds is covered on the day it
    is added, with no edit here.

    Line tables are correctly absent: `sale_items`, `return_items` and
    `purchase_order_items` carry no `company_id` of their own and scope
    entirely through their parent row -- the same shape `cash_movements`
    uses via `session_id`. If one of them ever shows up in this list, it
    means somebody added a redundant second copy of the tenant key.
    """
    scoped = []
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall():
        name = row[0]
        cols = {c[1] for c in conn.execute(f'PRAGMA table_info("{name}")').fetchall()}
        if 'company_id' in cols:
            scoped.append(name)
    return tuple(scoped)


def rebind_company_id(conn, new_company_id, old_company_id=None):
    """Move every tenant-scoped row in retail.db from one `company_id` to
    another, in ONE transaction, with the row counts verified on both sides.

    Reusable and idempotent by design, NOT inline migration code, because it
    has two callers that fire at completely different moments: the v14
    migration step (for an install that is already licensed when it upgrades)
    and `rebind_company_id_after_activation()` (for the far more common
    install that activates months later -- licensing is OFF by default in
    this product, so most installs migrate long before Owner has issued them
    anything).

    Returns a status dict, never a bare bool: `{'status': ..., 'rows': ...,
    'old_company_id': ..., 'new_company_id': ..., 'tables': (...)}`, where
    status is one of:
      - 'skipped'       -- no `new_company_id` supplied. A no-op, NOT an
                           error: the unlicensed install is the normal case.
      - 'already_bound' -- every scoped row is already on `new_company_id`.
      - 'rebound'       -- rows moved; `rows` says how many.

    IDENTIFYING THE OLD ID. When `old_company_id` is not supplied it is
    derived from the rows themselves -- the distinct set of `company_id`
    values actually present. Deriving it from the rows (rather than from the
    registry, or from config) is what makes an interrupted rebind
    self-healing: a crash that moved `sales` but nothing else leaves the set
    as {old, new}, and the next call finishes the job instead of concluding
    that everything is already done.

    Exactly three cases are safe to act on without being told:
      {}            -> nothing to move.
      {new}         -> already bound.
      {new?, old}   -> one other value; that is the tenant to move.
    Anything else RAISES CompanyRebindError and changes nothing. This is the
    multi-tenant guard, and it is not theoretical: CLAUDE.md states plainly
    that one install can host more than one company. Blanket-moving every row
    onto one licence's id would merge two companies' books into a single
    tenant -- unrecoverable, and it would look exactly like a successful
    migration. A caller that genuinely knows which tenant to move passes
    `old_company_id` explicitly.

    ONE TRANSACTION, NOT THIRTEEN. A database with six tables moved and seven
    not is precisely the silent-invisibility state this whole exercise exists
    to prevent, and it produces no error anywhere -- `_cid()` just stops
    matching. The rewrite therefore runs inside an explicit
    `BEGIN IMMEDIATE`, verifies afterwards (still inside the transaction)
    that every scoped table has zero rows left on the old id and that the
    new id gained exactly the rows the old id lost, and rolls the whole thing
    back on any mismatch or exception. IMMEDIATE rather than a deferred
    BEGIN so the write lock is taken up front: this rewrites every business
    row in the file, and discovering a competing writer halfway through is
    strictly worse than failing before the first UPDATE.

    Any in-flight implicit transaction is committed before that BEGIN --
    Python's sqlite3 opens one automatically on DML, so the v13 step running
    just before this in `_migrate_retail_schema` leaves one open, and
    `BEGIN` inside a transaction is an error. Committing it is safe and
    matches what the rebuild migrations earlier in this file already do:
    `ensure_schema_version` does not hold a transaction across the chain, and
    it advances `user_version` only on full success, so a failure here still
    re-runs every (idempotent) step on the next launch.
    """
    if new_company_id is None or (isinstance(new_company_id, str) and not new_company_id.strip()):
        return {
            'status': 'skipped',
            'reason': 'no Owner-issued company_id available',
            'rows': 0,
            'old_company_id': old_company_id,
            'new_company_id': new_company_id,
            'tables': (),
        }

    tables = company_scoped_tables(conn)
    if not tables:
        return {
            'status': 'skipped',
            'reason': 'no company-scoped tables in this database',
            'rows': 0,
            'old_company_id': old_company_id,
            'new_company_id': new_company_id,
            'tables': (),
        }

    present = set()
    for table in tables:
        for row in conn.execute(
            f'SELECT DISTINCT company_id FROM "{table}" WHERE company_id IS NOT NULL'
        ).fetchall():
            present.add(row[0])

    if old_company_id is None:
        others = {value for value in present if value != new_company_id}
        if not others:
            return {
                'status': 'already_bound',
                'rows': 0,
                'old_company_id': None,
                'new_company_id': new_company_id,
                'tables': tables,
            }
        if len(others) > 1:
            raise CompanyRebindError(
                f'Refusing to rebind company_id: this database holds rows under '
                f'{len(others)} different tenant keys ({sorted(map(repr, others))}). '
                f'Guessing which one the licence belongs to would merge two companies '
                f'books into one, irreversibly. Pass old_company_id explicitly.'
            )
        old_company_id = next(iter(others))

    before_old = {
        table: conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE company_id=?', (old_company_id,)
        ).fetchone()[0]
        for table in tables
    }
    before_new = {
        table: conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE company_id=?', (new_company_id,)
        ).fetchone()[0]
        for table in tables
    }
    before_total = {
        table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in tables
    }
    if sum(before_old.values()) == 0:
        return {
            'status': 'already_bound',
            'rows': 0,
            'old_company_id': old_company_id,
            'new_company_id': new_company_id,
            'tables': tables,
        }

    if conn.in_transaction:
        conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    try:
        moved = 0
        for table in tables:
            cur = conn.execute(
                f'UPDATE "{table}" SET company_id=? WHERE company_id=?',
                (new_company_id, old_company_id),
            )
            moved += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

        for table in tables:
            stranded = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE company_id=?', (old_company_id,)
            ).fetchone()[0]
            landed = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE company_id=?', (new_company_id,)
            ).fetchone()[0]
            total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if stranded or landed != before_old[table] + before_new[table] or total != before_total[table]:
                raise CompanyRebindError(
                    f'company_id rebind failed its own count check on {table!r}: '
                    f'{stranded} row(s) left on the old key, {landed} on the new '
                    f'(expected {before_old[table] + before_new[table]}), '
                    f'{total} rows total (expected {before_total[table]}). '
                    f'Rolled back -- the tenant key is unchanged.'
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        'status': 'rebound',
        'rows': moved,
        'old_company_id': old_company_id,
        'new_company_id': new_company_id,
        'tables': tables,
    }


def owner_issued_company_id(app_data_dir=None):
    """The Owner-issued tenant key for this install, or None.

    That value is `license_public_id` from the last assertion this install
    verified and persisted. Established by reading Owner, not by assumption:
    `owner/app/sync/routes.py` scopes the entire event stream by
    `SyncEvent.license_id`, resolved server-side from the VERIFIED
    installation and never from body input, and
    `owner/app/licensing_service/assertions.py` signs that same value into
    every assertion as `"license_public_id": str(license_row.id)`. So the
    licence -- not the installation -- is Owner's tenant.

    `installation_public_id` is deliberately NOT used even though it is also
    Owner-issued and sits in the same payload: it identifies one DEVICE's
    installation, so adopting it as `company_id` would give every terminal in
    a shop a different tenant key. That is the tenant-fragmentation failure
    this rebind exists to end, not a way to implement it.

    Read with plain sqlite3 + json, with NO import of
    `commercial_runtime.licensing_contracts`. That package imports
    `cryptography` at module scope, and an eager import of it crashed the
    whole Android backend with ModuleNotFoundError -- the same reason
    `device_context.local_device_fingerprint()` reads the licensing system's
    `device_key_meta.json` as plain JSON rather than importing the module
    that writes it. This function is a read-only consumer of somebody else's
    file, never its authority, so every failure (missing file, unreadable
    file, no assertion yet, malformed JSON) is reported the same honest way:
    None.

    The persisted state's `current_state` is deliberately NOT consulted. A
    lapsed subscription or an expired assertion does not make a shop's rows
    belong to a different tenant, and refusing to name the tenant while the
    licence is in a warning state would mean the rebind could never converge
    for exactly the installs most likely to need support.
    """
    if app_data_dir:
        path = os.path.join(app_data_dir, 'database', 'subsystems', 'licensing.db')
    else:
        path = os.path.join(SUBSYS_DIR, 'licensing.db')
    # Existence-checked before connecting: sqlite3.connect CREATES the file,
    # and an empty licensing.db conjured up by a read is exactly the kind of
    # side effect that makes "is this install licensed?" ambiguous later.
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path, timeout=5)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            'SELECT assertion_envelope_json FROM licensing_state WHERE id=1'
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    # ONE try, covering the parse AND both lookups, and typed at every step
    # rather than trusted.
    #
    # This guard used to stop one line short: `payload = ...get('payload')`
    # was inside it and `value = payload.get('license_public_id')` was
    # outside. An envelope whose `payload` is a list, a string or a number is
    # a perfectly legal JSON document -- `_json.loads(...).get('payload')`
    # returns it happily, it is truthy, and `.get` then raised AttributeError
    # one line later with nothing to catch it. `_json.loads` itself raises
    # TypeError, never listed here at all, when the column does not hold text
    # or bytes; the shipped table declares that column TEXT, whose affinity
    # converts an INTEGER to a string on the way in and hides the case, but
    # affinity is a property of one particular CREATE TABLE and this function
    # does not own this file.
    #
    # Either exception propagates through `_migrate_rebind_company_id_to_
    # owner_issued` -> `_migrate_retail_schema` -> `init_retail()` ->
    # `app.py::init_app()`, all unconditional, and `ensure_schema_version`
    # advances `user_version` only on full success. So the cost of a
    # malformed envelope was not "the tenant key could not be resolved" -- it
    # was the app never starting again with every future migration blocked
    # behind a frozen version marker. Exactly the same permanent wedge the
    # v13 duplicate-uid defect produced, reached from a different direction.
    #
    # `isinstance` on the payload instead of a bare `.get`, for the same
    # reason the return value is type-checked below: this is a read-only
    # consumer of somebody else's file, so a shape it cannot verify is a
    # shape it declines to use. The docstring above promises every failure is
    # reported the same honest way -- None -- and this is what makes that
    # true rather than aspirational.
    try:
        import json as _json
        envelope = _json.loads(row[0])
        if not isinstance(envelope, dict):
            return None
        payload = envelope.get('payload')
        if not isinstance(payload, dict):
            return None
        value = payload.get('license_public_id')
    except (ValueError, TypeError, AttributeError):
        return None
    if not value or not isinstance(value, str):
        return None
    return value


def local_authoritative_company_id():
    """The `company_id` this install's IDENTITY layer currently considers
    authoritative -- the value `mt_auth.create_session` puts into
    `session['company_id']` and therefore the one every
    `retail_api._cid()`-filtered query in the product compares against.

    Same source and same precedence order `sync_service.
    local_company_id_from_registry()` already uses (company_settings first,
    the admin `users` row as a fallback, None rather than a fabricated
    default), re-implemented here as a small read instead of imported,
    for two reasons: `commercial_runtime/sync/` is out of scope this phase
    and must not gain a new dependant, and `registry_db` caches its DB_PATH
    at import time -- fine for a real process, which imports it once, but
    wrong for anything that has to resolve AURA_APP_DATA freshly (the
    anti-pattern `device_context._resolve_app_data()` documents and
    products/run_all_tests.py's docstring explains at length).
    """
    app_data = os.environ.get('AURA_APP_DATA')
    path = (
        os.path.join(app_data, 'database', 'registry.db') if app_data
        else os.path.join(BASE_DIR, 'registry.db')
    )
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path, timeout=5)
    except sqlite3.Error:
        return None
    try:
        for sql in (
            'SELECT company_id FROM company_settings LIMIT 1',
            "SELECT company_id FROM users WHERE role='admin' LIMIT 1",
        ):
            try:
                row = conn.execute(sql).fetchone()
            except sqlite3.Error:
                continue
            if row and row[0]:
                return row[0]
        return None
    finally:
        conn.close()


def _rebind_to_owner_issued(conn, new_company_id):
    """Shared body of the v14 migration step and the activation-time hook.

    THE GUARD THIS FUNCTION EXISTS FOR, and the reason neither caller is a
    bare `rebind_company_id(conn, owner_issued_company_id())`:

    `session['company_id']` is populated from registry.db's `users` row
    (`commercial_runtime/identity/mt_auth.py::create_session`), and
    `retail_api._cid()` filters essentially every query in the product on it.
    Moving retail.db's rows onto the Owner-issued key while the identity
    layer still says `md5(admin_email)` therefore makes every
    `WHERE company_id=?` match zero rows: a real shop's entire history
    disappears from the UI, with no exception, no failed integrity_check and
    no log line. That is strictly worse than not rebinding at all.

    So the retail side only ever CONVERGES onto a tenant key the identity
    layer has ALREADY adopted. This is the same direction of travel
    `sync_service._apply_event` already takes when it ignores a pulled
    payload's `company_id` and stamps the RECEIVING device's own value
    instead -- and it is what makes an interrupted two-database rebind
    self-healing rather than fatal: if identity moved and retail did not,
    the next call (next launch, or the next activation) finishes the job,
    because `rebind_company_id` derives the old id from retail's own rows.

    Until the identity-side rebind exists, this returns 'deferred' and
    changes nothing. That is an honest no rather than a half-applied yes.
    """
    authoritative = local_authoritative_company_id()
    if authoritative is None or str(authoritative) != str(new_company_id):
        return {
            'status': 'deferred',
            'reason': (
                'the identity layer still reports company_id='
                f'{authoritative!r}, not the Owner-issued {new_company_id!r}; '
                'rebinding retail.db first would hide every row from _cid()'
            ),
            'rows': 0,
            'old_company_id': authoritative,
            'new_company_id': new_company_id,
            'tables': (),
        }
    return rebind_company_id(conn, new_company_id)


def _migrate_rebind_company_id_to_owner_issued(conn):
    """One-time migration (schema v14): rebind `company_id` onto the
    Owner-issued tenant key -- launch-readiness Phase 2, reserved in
    ROADMAP.md's 2026-08-21 ledger.

    v14 is about being ABLE to rebind, not about having done it. Licensing is
    OFF by default in this product (config.py: with OWNER_LICENSING_BASE_URL
    unset the app runs fully unlocked and there IS no Owner-issued id), so
    the overwhelmingly common install reaches this step with nothing to
    rebind to. That is a no-op, not an error, and `user_version` still
    advances -- otherwise every later migration would sit behind a version
    marker that never moves on the majority of installs.

    An install that activates a licence LATER does not get missed, because
    the same work is reachable from `rebind_company_id_after_activation()`
    (wired into the activation route via
    `make_licensing_blueprint(on_activation_success=...)`).

    `CompanyRebindError` is caught HERE and only here. Raising it out of a
    migration would leave a multi-tenant install unable to advance its schema
    version ever again -- every future migration blocked by a refusal that is
    itself correct. The direct `rebind_company_id()` API still raises, so a
    caller that asked for the rebind explicitly still hears about it.
    """
    new_company_id = owner_issued_company_id()
    if not new_company_id:
        return {
            'status': 'skipped',
            'reason': 'this install has no Owner-issued licence assertion',
            'rows': 0,
            'old_company_id': None,
            'new_company_id': None,
            'tables': (),
        }
    try:
        return _rebind_to_owner_issued(conn, new_company_id)
    except CompanyRebindError as exc:
        return {
            'status': 'deferred',
            'reason': str(exc),
            'rows': 0,
            'old_company_id': None,
            'new_company_id': new_company_id,
            'tables': (),
        }


def rebind_company_id_after_activation():
    """Activation-time entry point: called after a licence activation
    succeeds, so an install that activates months after it migrated gets
    rebound at that moment instead of waiting for a next migration that may
    never come.

    Opens its own connection -- the activation request has none, and it is
    handled by `commercial_runtime/licensing_contracts/routes.py`, which
    never imports anything product-specific. Retail's `app.py` passes this
    function in as `make_licensing_blueprint(on_activation_success=...)`,
    which keeps that package's product-agnostic boundary intact.

    NEVER RAISES. A licence activation that genuinely succeeded at Owner must
    not be reported back to the customer as a failure because a local
    bookkeeping rewrite hit a locked database -- the customer would retry the
    activation, which is the one action that cannot fix it. Every outcome is
    a status dict; the work is idempotent and reachable again from the next
    activation, check-in-triggered call, or migration.
    """
    try:
        new_company_id = owner_issued_company_id()
        if not new_company_id:
            return {
                'status': 'skipped',
                'reason': 'activation left no assertion carrying a license_public_id',
                'rows': 0,
                'old_company_id': None,
                'new_company_id': None,
                'tables': (),
            }
        conn = get_retail_conn()
        try:
            result = _rebind_to_owner_issued(conn, new_company_id)
            conn.commit()
            return result
        finally:
            conn.close()
    except Exception as exc:
        return {
            'status': 'failed',
            'reason': f'{type(exc).__name__}: {exc}',
            'rows': 0,
            'old_company_id': None,
            'new_company_id': None,
            'tables': (),
        }


# ── v15: making the cache provably derivable from the ledger ────────────────

#: The movement type v15 writes for a balance that had no ledger history.
#:
#: Deliberately NOT `opening_stock`. That type means "an operator declared
#: this opening figure", and api/import_api.py sums exactly those rows (plus
#: its own import reference) to work out how much of a product's stock has
#: already been declared, so it can post a DELTA instead of re-adding the
#: whole figure on every re-import. These rows were declared by nobody: the
#: migration inferred them from the cached balance. Filing an inference under
#: the type reserved for a human declaration would make the importer treat a
#: guess as evidence, which is the same category of mistake as stamping a
#: real user on the row.
V15_OPENING_COUNT_TYPE = 'opening_count'

#: Reference marker on the seeded rows, so they are one indexed query away
#: from any screen, report or later migration that needs to tell an inferred
#: opening count apart from a counted one.
V15_OPENING_COUNT_REFERENCE = 'V15-OPENING-COUNT'

#: The reason, stored on the row itself rather than only in this file. A
#: number in a goods ledger with no explanation attached is exactly how the
#: next person concludes somebody counted this stock.
V15_OPENING_COUNT_REASON = (
    'Opening count inferred by schema migration v15: the cached balance minus '
    'everything the ledger already accounts for at this product/branch. This '
    'is what must have been on hand before the recorded history for that '
    'history to end where the balance says it does. Nobody counted it.'
)

#: How many seeded movements are handed to one executemany. The candidate
#: list is bounded by products x branches (not by transaction history, which
#: is orders of magnitude larger), so it is materialised in one query -- but
#: the INSERT is still sliced, because this runs on a till machine.
_V15_SEED_CHUNK = 2000

#: Rows named individually in the gate's failure message. The full list is a
#: whole-catalogue dump; the first few are what makes the message actionable.
_V15_REPORTED_OFFENDERS = 5

#: How many product ids go into one `IN (...)` when the seeder re-checks that
#: its candidates still have a product row. Well under SQLite's 999-variable
#: floor (SQLITE_MAX_VARIABLE_NUMBER was raised to 32766 only in 3.32), so
#: this holds on whatever SQLite the packaged interpreter happens to carry.
_V15_PRODUCT_PROBE_CHUNK = 500


def _v15_company_ids(conn):
    """Every company_id that has stock state at all, from BOTH sides.

    UNION rather than a read of `inventory_balances` alone: a company whose
    balances were all deleted still has movements, and it is exactly that
    company whose drift nobody would otherwise look at.
    """
    return [row[0] for row in conn.execute(
        "SELECT company_id FROM inventory_balances "
        "UNION "
        "SELECT company_id FROM inventory_movements"
    ).fetchall()]


def _v15_resolve_unambiguous_null_branches(conn, company_id, live_tables):
    """Give legacy NULL-branch movements a branch ONLY where there is exactly
    one branch they could belong to. Returns how many rows moved.

    `inventory_balances.branch_id` is NOT NULL, so a movement with a NULL
    branch has no balance row that could ever hold it and `compute_drift`
    reports it as `repairable: False` forever. A company with exactly ONE
    branch has one answer and it is not a guess. A company with two has two,
    and picking either puts real stock in the wrong shop -- worse than
    leaving it visibly unassigned, because a wrong number looks right.

    Counts ALL branches, not just active ones: an archived branch is still
    somewhere the stock could have been, so its existence makes the answer
    ambiguous even though nobody sells there any more.

    Runs BEFORE the opening-count seeding, and the order is load-bearing.
    Resolving a NULL-branch movement onto branch B gives (product, B) ledger
    history; seeding first would have already written an opening count for
    that key on the grounds that it had none, and the two would then sum to
    double the stock and fail the gate.
    """
    if 'branches' not in live_tables:
        return 0
    pending = conn.execute(
        "SELECT COUNT(*) FROM inventory_movements "
        "WHERE company_id=? AND branch_id IS NULL", (company_id,)
    ).fetchone()[0]
    if not pending:
        return 0
    branch_ids = [row[0] for row in conn.execute(
        "SELECT id FROM branches WHERE company_id=?", (company_id,)
    ).fetchall()]
    if len(branch_ids) != 1:
        return 0        # zero branches: nowhere to put them. Two or more: a guess.
    conn.execute(
        "UPDATE inventory_movements SET branch_id=? "
        "WHERE company_id=? AND branch_id IS NULL",
        (branch_ids[0], company_id),
    )
    return pending


def _v15_seed_opening_counts(conn, company_id, movement_columns, drifted):
    """One `opening_count` movement carrying the RESIDUAL for every key the
    ledger cannot yet explain. `drifted` is `compute_drift`'s output AFTER
    the NULL-branch resolve step.

    THE RESIDUAL, NOT THE ABSENCE OF HISTORY. This function originally seeded
    only keys with ZERO movements, and that was the single most dangerous
    defect this phase produced. The importer used to write an absolute
    `SET quantity_on_hand=?` with NO movement row (git 13c1c92,
    api/import_api.py:1146), and every sale since has written one -- so the
    COMMON legacy key has sales and no opening. It was skipped, the gate
    refused, `app.py`'s unconditional `init_retail()` meant the POS would not
    boot, and the refusal's own recovery advice then completed the disaster:
    `repair_drift` saw real ledger history (the sales), so
    SKIP_NO_LEDGER_HISTORY never fired, and it wrote the sales-only sum as
    the balance. Measured end to end: shelves [112, 57, 7] -> refused ->
    "repaired 3, skipped 0" -> shelves [-8, -3, -1] -> relaunch advances
    reporting zero drift. A shop's stock, destroyed by following the
    instructions.

    An opening count is "what must have been on the shelf before the recorded
    history, for that history to end where the balance says it does". That is
    the residual -- `quantity_on_hand` minus the ledger total at the key --
    by definition, and the zero-movement case is just its special case where
    the ledger total is zero. `_seed_retail` at the bottom of this file had
    the correct expression all along; this is the same one.

    The residual is `compute_drift`'s `drift`, so the candidates come FROM
    `compute_drift` rather than from a second copy of its key-set SQL. That
    is not merely tidy: the gate is evaluated on `compute_drift`'s output, so
    seeding from anything else would let the two disagree about which keys
    exist -- which is exactly how the zero-movement version got it wrong.

    ONLY NON-NEGATIVE RESIDUALS ARE SEEDED, and this is what keeps the gate
    real rather than softening it into decoration. A NEGATIVE residual says
    the ledger accounts for MORE goods than the shelf shows, and no opening
    count explains that: "the shop began with minus eight units" is not a
    fact about a shop. It is shrinkage, theft, or a writer that decremented a
    balance without recording why -- a human decision, not a migration's
    inference. Those rows fall through to the gate and are refused, and
    `repair_drift` can resolve them because doing so RAISES the balance to a
    total the ledger can prove arrived.

    Idempotent by construction, still: after this run the residual at every
    seeded key is zero, so a second run's `compute_drift` yields nothing to
    seed. Nothing is keyed off the seeded rows' own marker, so a shop that
    later posts a real movement is unaffected either way.

    Zero-quantity balances with NO history at all are seeded too, at zero.
    A zero balance with no movements behind it is not "counted, found
    nothing" -- it is "never counted", and after v15 the difference has to be
    visible in the ledger rather than inferred from an absence. It is also
    what stops `repair_drift` refusing the key forever for
    SKIP_NO_LEDGER_HISTORY. These keys have no drift, so `compute_drift`
    never reports them and they need the second query below.

    WHAT THE ROW DOES NOT CLAIM: `actor_user_uid` and `terminal_id` stay
    NULL, and `created_by` is the schema's existing non-person sentinel.
    Nobody performed this count and this device cannot prove which terminal
    would have. `created_at_utc` IS stamped, and the contrast with v13 --
    which left `created_at_utc` NULL on every history row it touched -- is
    deliberate rather than inconsistent: v13's rows already existed and their
    real instant was unknowable, while these rows are being created right
    now, so their creation time is a fact about them. `unit_cost` is left at
    its default: the shop's cost_price today is not evidence of what this
    stock cost when it arrived, and valuation reading a made-up figure is
    worse than valuation reading a zero it can see.
    """
    import uuid as _uuid
    from core.retail.stock_reconciliation import DEFAULT_TOLERANCE

    # `repairable` already excludes both structural impossibilities: a
    # NULL branch (no balance row could hold it) and a product row that is
    # gone (`inventory_movements.product_id` has a FOREIGN KEY to
    # products(id) and `_conn()` turns enforcement ON, so the INSERT below
    # would raise IntegrityError and kill the migration on "FOREIGN KEY
    # constraint failed" -- a message that says nothing about stock and
    # leaves the operator with a crashing app). See compute_drift.
    pending = {
        (row['product_id'], row['branch_id']): float(row['drift'])
        for row in drifted
        if row['repairable'] and row['drift'] > DEFAULT_TOLERANCE
    }

    # The never-counted keys `compute_drift` cannot report, because they do
    # not drift: a balance of zero with no ledger history at all.
    #
    # `branch_id IS b.branch_id` rather than `=`: movements' branch_id is
    # nullable, and `=` against NULL is NULL (never true), which would make
    # an unresolved NULL-branch movement look like "no history" and earn the
    # balance a duplicate opening count on every single run.
    for product_id, branch_id, quantity in conn.execute(
        "SELECT b.product_id, b.branch_id, b.quantity_on_hand "
        "FROM inventory_balances b "
        "WHERE b.company_id=? "
        "  AND NOT EXISTS ("
        "      SELECT 1 FROM inventory_movements m "
        "      WHERE m.company_id=b.company_id "
        "        AND m.product_id=b.product_id "
        "        AND m.branch_id IS b.branch_id)"
        "  AND EXISTS (SELECT 1 FROM products p WHERE p.id=b.product_id)",
        (company_id,),
    ).fetchall():
        key = (product_id, branch_id)
        if key in pending:
            continue        # already carrying its residual from the drift set
        value = float(quantity or 0)
        if value < 0:
            continue        # a negative opening count is not an opening count
        pending[key] = value

    if not pending:
        return 0

    # ONE LAST PROBE, and it is not redundant with `repairable` above.
    #
    # `inventory_movements.product_id` has a FOREIGN KEY to products(id) and
    # `_conn()` turns enforcement ON, so an INSERT for a product that is gone
    # raises IntegrityError and kills the whole migration on "FOREIGN KEY
    # constraint failed" -- a message that says nothing about stock and
    # leaves the operator with an app that will not start and no idea why.
    # The `repairable` filter already excludes those keys, so this can only
    # fire if `compute_drift` and this function ever disagree about what a
    # missing product is. That is exactly the kind of coupling that goes
    # quietly wrong later, and a migration is the worst possible place to
    # find out: the cost of being wrong here is a crash loop, the cost of the
    # probe is one indexed lookup per candidate product. A test that
    # deliberately mis-classifies an orphan as repairable found this.
    candidates = sorted({key[0] for key in pending}, key=str)
    live = set()
    for start in range(0, len(candidates), _V15_PRODUCT_PROBE_CHUNK):
        chunk = candidates[start:start + _V15_PRODUCT_PROBE_CHUNK]
        live.update(row[0] for row in conn.execute(
            f"SELECT id FROM products WHERE id IN ({','.join('?' * len(chunk))})",
            tuple(chunk)).fetchall())
    pending = {key: value for key, value in pending.items() if key[0] in live}

    if not pending:
        return 0

    columns = ['company_id', 'product_id', 'branch_id', 'movement_type',
               'quantity', 'reference', 'notes', 'created_by']
    # v13 columns, present on every real install by the time this runs (v13 is
    # earlier in the same chain) but absent from hand-built minimal fixtures,
    # so they are added only when the live table actually has them.
    optional = [c for c in ('uid', 'created_at_utc') if c in movement_columns]
    columns.extend(optional)
    sql = (f"INSERT INTO inventory_movements ({','.join(columns)}) "
           f"VALUES ({','.join('?' * len(columns))})")

    stamp = datetime.now(timezone.utc).isoformat()
    rows = []
    # Sorted so a run is reproducible and two installs with the same data
    # seed in the same order -- dict iteration order is insertion order here,
    # which is compute_drift's ORDER BY followed by the second query's, and
    # depending on that would be depending on an implementation detail.
    #
    # `str(product_id)` because v13 moved products off autoincrement ids onto
    # uuids and a database mid-migration can hold both, which sort against
    # each other with a TypeError rather than an ordering. `branch_id` cannot
    # be NULL among these candidates (both sources exclude it) but is
    # defended anyway: a sort key is a terrible place to learn otherwise.
    for (product_id, branch_id), quantity in sorted(
            pending.items(),
            key=lambda item: (str(item[0][0]),
                              -1 if item[0][1] is None else item[0][1])):
        values = [company_id, product_id, branch_id, V15_OPENING_COUNT_TYPE,
                  quantity, V15_OPENING_COUNT_REFERENCE,
                  V15_OPENING_COUNT_REASON, 'System']
        for column in optional:
            # A real RFC-4122 uuid4 per row, generated in Python -- never
            # SQLite's lower(hex(randomblob(16))), which is 32 characters of
            # something that is not a UUID and that Owner's sync ingest
            # rejects at the point it is most expensive to discover. Same
            # decision, same reason, as v13's uid backfill.
            values.append(str(_uuid.uuid4()) if column == 'uid' else stamp)
        rows.append(tuple(values))

    for start in range(0, len(rows), _V15_SEED_CHUNK):
        conn.executemany(sql, rows[start:start + _V15_SEED_CHUNK])
    return len(rows)


def _v15_record_outcome(conn, company_id, summary, live_tables):
    """Write what v15 did, and what it deliberately did not do, to audit_log.

    `user_id` is NULL on purpose: no person seeded these counts or declined
    to assign those branches, and audit_log's `user_id` is the one place a
    reader looks to find out who did. The unassigned and orphaned counts are
    recorded here because "the gate passed" and "the gate passed while
    excluding four rows it could not place and two whose products no longer
    exist" are different facts, and only one of them is true.

    An observation, not state: re-running the step (which only happens while
    the version marker is still behind) appends a second observation rather
    than editing the first. The ledger, the balances and the branch
    assignments are what must be idempotent, and they are.
    """
    if 'audit_log' not in live_tables:
        return
    if not (summary['seeded'] or summary['branches_resolved']
            or summary['unassigned'] or summary['orphaned']):
        return
    import json as _json
    conn.execute(
        "INSERT INTO audit_log (company_id,user_id,action,entity,entity_id,details) "
        "VALUES (?,?,?,?,?,?)",
        (company_id, None, 'STOCK_LEDGER_SEEDED_V15', 'inventory_movements', None,
         _json.dumps(summary, sort_keys=True, default=str)),
    )


def _v15_gate_message(refusals):
    """The refusal, written to be acted on.

    "Migration failed" is not a report about a shop's stock. Every company
    that could not be reconciled is named, with the rows that could not be,
    and with the one recovery that is supported -- because an operator who
    reaches this cannot open the app to look, and the message is all they
    have.
    """
    parts = []
    for company_id, blocking, summary in refusals:
        net = round(sum(row['drift'] for row in blocking), 4)
        offenders = '; '.join(
            f"{row['sku'] or row['product_id']} @ branch {row['branch_id']}: "
            f"balance {row['stored_balance']} vs ledger {row['ledger_balance']} "
            f"(drift {round(row['drift'], 4)})"
            for row in blocking[:_V15_REPORTED_OFFENDERS]
        )
        more = ('' if len(blocking) <= _V15_REPORTED_OFFENDERS
                else f" ...and {len(blocking) - _V15_REPORTED_OFFENDERS} more")
        parts.append(
            f"company {company_id}: {len(blocking)} (product, branch) balance(s) "
            f"account for LESS stock than inventory_movements records arriving, "
            f"net drift {net} [{offenders}{more}]; "
            f"{summary['seeded']} opening count(s) seeded and committed; "
            f"recover with core.retail.stock_reconciliation.repair_drift(conn, "
            f"{company_id!r})"
        )
    return (
        "Retail schema v15 refused to advance PRAGMA user_version. "
        + ' | '.join(parts) + ". "
        "This is NOT a broken migration, and it is not the ordinary legacy shape "
        "either -- v15 already inferred and seeded an opening count for every key "
        "whose balance merely exceeded what the ledger knew about. What is left is "
        "the opposite: the ledger records MORE goods arriving than the shelf claims "
        "to hold, and no opening count explains that, because a shop cannot have "
        "started with less than nothing. It is shrinkage, loss, or a writer that "
        "decremented a balance without recording why -- the inconsistency Phase 3 "
        "exists to surface and the one multi-device sync must never spread. "
        "The repair above RAISES each of these balances to the total the ledger can "
        "prove arrived (it takes its own transaction and writes its own audit row) "
        "and destroys no stock; if the goods really are gone, post an adjustment "
        "movement for the loss instead, which is the honest record. Either way, "
        "relaunch afterwards and v15 will re-run and pass. Do not relax this check: "
        "it converts a detected problem into an undetected one."
    )


def _migrate_seed_opening_counts_and_gate_drift(conn):
    """One-time migration (schema v15): make `inventory_balances` provably
    derivable from `inventory_movements` -- launch-readiness Phase 3
    (docs/launch-readiness/phase3-ledger-truth.md), reserved in ROADMAP.md's
    2026-08-21 ledger.

    Additive only, like v13 and v14: one INSERT per unbacked balance, one
    guarded UPDATE for unambiguous NULL branches, one CREATE INDEX IF NOT
    EXISTS. No table is rebuilt, no row is deleted, no existing quantity is
    rewritten -- in particular this step never touches
    `inventory_balances.quantity_on_hand`. It moves the LEDGER to explain the
    cache, never the cache to match the ledger: overwriting a balance is a
    repair, repair destroys the evidence that a writer is broken, and repair
    is owner-initiated by design (see core/retail/stock_reconciliation.py's
    module docstring).

    THE ORDER OF THE FIVE STEPS IS THE DESIGN:

    1. measure BEFORE, so the run can say what it found rather than only what
       it left;
    2. resolve NULL branches where one branch makes it unambiguous;
    3. RE-measure, because step 2 moved ledger history onto a branch and so
       changed the residual there;
    4. seed one opening count carrying that residual per unexplained key;
    5. measure AFTER and GATE.

    STEPS 2 AND 3 ARE NOT INTERCHANGEABLE WITH 4. Resolving a NULL-branch
    movement onto branch B gives (product, B) ledger history it did not have;
    seeding first would have written an opening count computed without it,
    and the two would sum to the wrong stock and fail the gate. The
    re-measure between them exists for the same reason at the level of the
    number rather than the key.

    STEP 2 IS THE ONLY NON-ADDITIVE WRITE IN THE MIGRATION, and it earns its
    place. A verifier found that on a single-branch shop with pre-branch NULL
    history it was the step that caused the refusal -- but that was true only
    while step 4 seeded the ABSENCE of history rather than the residual: the
    resolve gave the key history, the old seeder therefore skipped it, and
    the gate refused a shop whose answer was never in doubt. With the
    residual it is the resolve that makes the shop CORRECT rather than merely
    unblocked: the sales land on the one branch they can have happened at,
    the opening count is computed against them, and the stock is intact.
    Deleting the step would also have worked -- every NULL row to the queue,
    still advancing, still simpler -- and it was rejected because it is worse
    for the common install: a single-branch shop is the overwhelmingly likely
    legacy shape, and it would be handed a permanent "needs assignment" queue
    whose every entry has exactly one possible answer. A queue nobody can
    resolve wrongly is not a safety feature, it is a chore.

    THE GATE IS ZERO DRIFT AMONG REPAIRABLE ROWS, NOT ZERO DRIFT.
    `compute_drift` reports `repairable: False` for the two STRUCTURAL
    impossibilities -- a movement whose branch_id is still NULL after step 2
    (`inventory_balances.branch_id` is NOT NULL, so no balance row could ever
    hold it) and a balance whose product row is gone
    (`inventory_movements.product_id` has a FOREIGN KEY to products(id), so
    no movement row can ever be written for it). A gate demanding global zero
    would mean an install carrying one such row could never advance its
    schema version AGAIN -- not for v15, not for anything after it. That is
    the permanent wedge the v13 duplicate-uid defect produced, and the orphan
    balance reached it from a third direction: the seeder skipped the row,
    the gate refused over it, and `repair_drift` refused it too, so three
    measured launch/repair/launch cycles left `user_version` at 14, 14, 14
    with nothing an operator could do. Such an install advances here carrying
    counted, recorded queues (`stock_reconciliation.unassigned_movements` and
    `orphan_balances`) instead of a guess or a brick.

    WHAT THE GATE STILL REFUSES, and it must: a key whose residual is
    NEGATIVE -- the ledger accounts for more goods than the shelf shows. No
    opening count explains that (a shop cannot begin with less than nothing),
    so `_v15_seed_opening_counts` deliberately leaves it alone and it reaches
    here. Softening this into "seed whatever closes the gap" would make the
    gate incapable of failing, which is the opposite failure and just as bad.

    WHY THE COMMIT IS WHERE IT IS. The seeding is committed BEFORE the gate
    can raise, which looks like a violation of "leave the database as the
    backup found it" and is the opposite. `ensure_schema_version` does not
    commit on the failure path, so without this the seeded opening counts
    would be discarded, and the recovery this failure names would run against
    a ledger that had been rolled back to silence. Seeding first, durably, is
    half of what makes `repair_drift` safe to point an operator at; the other
    half is `SKIP_LEDGER_NOT_ESTABLISHED`, which refuses to LOWER a balance
    at all until this gate has passed, so that even an operator who runs the
    repair on an install v15 has never touched cannot lose stock by it.
    Because every write here is idempotent and additive, committing it costs
    nothing if the next launch retries.

    Existence-guarded: some fixtures hand-build a minimal schema and call
    `_migrate_retail_schema` directly (see
    retail_category_delete_fk_sync_test.py, which builds no
    `inventory_balances` at all), and a migration that assumed the full
    schema would crash them.

    Returns {company_id: summary} for the caller that wants to report what
    happened; `_migrate_retail_schema` ignores it, and the durable record is
    the audit_log row (`_v15_record_outcome`).
    """
    live_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    # All four, not just the two this step writes: `compute_drift` LEFT JOINs
    # `products` and `branches` to name the rows it reports, so the gate
    # cannot run without them either. Every real install has all four from
    # `_init_retail`'s base executescript; what this guard is for is the
    # hand-built minimal fixtures that call `_migrate_retail_schema`
    # directly (retail_category_delete_fk_sync_test.py builds only
    # categories/products/inventory_movements), where skipping is the
    # correct answer rather than a crash.
    if not {'inventory_balances', 'inventory_movements',
            'products', 'branches'} <= live_tables:
        return {}

    # `compute_drift` reads its rows by NAME. Every real caller comes through
    # `get_retail_conn()` (row_factory = sqlite3.Row), but a migration that
    # only works on a connection configured a particular way is a trap for
    # the next person calling it directly, so the factory is set here and
    # restored afterwards rather than assumed.
    from core.retail.stock_reconciliation import (
        SKIP_NO_BRANCH, SKIP_NO_PRODUCT, compute_drift)
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        # Both sides of `compute_drift`'s UNION, plus this step's own
        # NOT EXISTS probe and the importer's already-declared sum, look
        # rows up by exactly this triple and there was no index on it.
        # Created here rather than in the base executescript so a pre-v15
        # install gets it too.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inventory_movements_key "
            "ON inventory_movements(company_id, product_id, branch_id)"
        )
        movement_columns = {
            row[1] for row in conn.execute('PRAGMA table_info("inventory_movements")').fetchall()
        }

        outcomes, refusals = {}, []
        for company_id in _v15_company_ids(conn):
            before = compute_drift(conn, company_id)
            resolved = _v15_resolve_unambiguous_null_branches(conn, company_id, live_tables)
            # Re-measured AFTER the resolve, because the resolve MOVES ledger
            # history onto a branch and therefore changes the residual at
            # that key. Seeding from `before` would seed the pre-resolve
            # figure and double-count by exactly the resolved rows.
            resolvable = compute_drift(conn, company_id)
            seeded = _v15_seed_opening_counts(
                conn, company_id, movement_columns, resolvable)
            after = compute_drift(conn, company_id)

            blocking = [row for row in after if row['repairable']]
            unassigned = [row for row in after
                          if row['blocked_reason'] == SKIP_NO_BRANCH]
            orphaned = [row for row in after
                        if row['blocked_reason'] == SKIP_NO_PRODUCT]
            summary = {
                'company_id': company_id,
                'drift_rows_before': len(before),
                'net_drift_before': round(sum(row['drift'] for row in before), 4),
                'branches_resolved': resolved,
                'seeded': seeded,
                'unassigned': len(unassigned),
                'net_drift_unassigned': round(sum(row['drift'] for row in unassigned), 4),
                'orphaned': len(orphaned),
                'net_drift_orphaned': round(sum(row['drift'] for row in orphaned), 4),
                'drift_rows_after': len(blocking),
            }
            _v15_record_outcome(conn, company_id, summary, live_tables)
            outcomes[company_id] = summary

            if blocking:
                # Collected, not raised here. One install CAN host more than
                # one company, and stopping at the first refusal would leave
                # every later company's seeding undone and its drift
                # undiscovered until the operator had fixed this one and
                # relaunched -- learning about the next shop one relaunch at
                # a time. Every company's honest work lands; the refusal at
                # the end names all of them.
                refusals.append((company_id, blocking, summary))

        # Durable BEFORE any refusal -- see this function's docstring. This
        # commit is what makes the recovery the message recommends safe.
        conn.commit()
        if refusals:
            raise RetailLedgerDriftError(_v15_gate_message(refusals))
        return outcomes
    finally:
        conn.row_factory = previous_factory


def init_retail():
    """Create/upgrade retail.db and seed it on first boot.

    A thin wrapper around `_init_retail` so the connection is closed on EVERY
    exit, including the exceptional one. It previously was not: the single
    `conn.close()` lived at the end of the body, so a migration that raised
    skipped it entirely and left the connection alive inside the propagating
    traceback -- frames keep their locals, and the caller
    (`app.py::init_app()`, which calls this unconditionally) lets the
    exception through to be logged, which keeps the traceback alive too.

    The consequence was a misdiagnosis, not just a leak. That connection is
    holding the WAL write lock it took when the migration's first DML opened
    Python's implicit transaction, so the NEXT launch failed with "database
    is locked" -- a message that points at concurrency and says nothing at
    all about the migration that actually broke. A support engineer reading
    it goes looking for a second copy of the app.

    The rollback is conditional on `in_transaction` rather than
    unconditional: on the success path everything downstream has already
    committed (`ensure_schema_version` commits after the migrate step and
    again after advancing `user_version`; `_seed_retail` commits its own
    work), so an unconditional rollback would be a no-op that reads like a
    discard. On the failure path it releases the write lock explicitly
    instead of relying on close() to do it.
    """
    conn = get_retail_conn()
    try:
        _init_retail(conn)
    finally:
        try:
            if conn.in_transaction:
                conn.rollback()
        except sqlite3.Error:
            # Never let cleanup replace the real exception with a worse one:
            # whatever went wrong in the migration is the thing the operator
            # needs to read, and close() below releases the lock regardless.
            pass
        conn.close()


def _init_retail(conn):
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS branches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        address TEXT,
        phone TEXT,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        sku TEXT NOT NULL,
        barcode TEXT,
        name TEXT NOT NULL,
        category_id INTEGER,
        cost_price REAL DEFAULT 0,
        sell_price REAL DEFAULT 0,
        tax_rate REAL DEFAULT 0,
        unit TEXT DEFAULT 'pcs',
        reorder_level INTEGER DEFAULT 5,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        -- ON DELETE SET NULL, not a bare REFERENCES: a category delete
        -- relayed in from ANOTHER device must never be able to fail against
        -- this device's own product links (products are not synced, so two
        -- devices on one license legitimately hold different product ->
        -- category assignments). See
        -- _migrate_products_category_fk_on_delete_set_null above.
        FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS inventory_movements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        product_id INTEGER NOT NULL,
        branch_id INTEGER,
        movement_type TEXT NOT NULL,
        quantity REAL NOT NULL,
        unit_cost REAL DEFAULT 0,
        reference TEXT,
        notes TEXT,
        created_by TEXT DEFAULT 'System',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS inventory_balances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        product_id INTEGER NOT NULL,
        branch_id INTEGER NOT NULL,
        quantity_on_hand REAL DEFAULT 0,
        quantity_reserved REAL DEFAULT 0,
        UNIQUE(company_id, product_id, branch_id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        address TEXT,
        loyalty_points REAL DEFAULT 0,
        total_spent REAL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        address TEXT,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        -- PO-preview-by-supplier foundation (schema v6): procurement
        -- metadata the split preview's MOQ warning reads. See
        -- _migrate_add_supplier_contacts_and_po_split below -- included
        -- here too so a brand-new install gets v6 shape directly without
        -- ever running that migration, same as sync_outbox/sync_cursor.
        min_order_value REAL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        po_number TEXT UNIQUE,
        supplier_id INTEGER,
        branch_id INTEGER,
        status TEXT DEFAULT 'pending',
        subtotal REAL DEFAULT 0,
        tax_total REAL DEFAULT 0,
        total REAL DEFAULT 0,
        notes TEXT,
        ordered_at TEXT,
        received_at TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        -- PO-preview-by-supplier foundation (schema v6): split-group/
        -- routing/idempotency columns -- the writers that populate them
        -- land later this week, this only adds the columns. See
        -- _migrate_add_supplier_contacts_and_po_split below -- included
        -- here too so a brand-new install gets v6 shape directly without
        -- ever running that migration, same as sync_outbox/sync_cursor.
        split_group_id TEXT,
        split_index INTEGER,
        split_count INTEGER,
        routing_status TEXT,
        routed_channel TEXT,
        routed_at TEXT,
        routed_to TEXT,
        idempotency_key TEXT,
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    );
    CREATE TABLE IF NOT EXISTS purchase_order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        po_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity REAL NOT NULL,
        unit_cost REAL NOT NULL,
        total REAL NOT NULL,
        received_qty REAL DEFAULT 0,
        FOREIGN KEY (po_id) REFERENCES purchase_orders(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        sale_number TEXT UNIQUE,
        branch_id INTEGER,
        customer_id INTEGER,
        cashier TEXT DEFAULT 'POS',
        subtotal REAL DEFAULT 0,
        discount_amount REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        total REAL DEFAULT 0,
        amount_paid REAL DEFAULT 0,
        change_amount REAL DEFAULT 0,
        payment_method TEXT DEFAULT 'cash',
        status TEXT DEFAULT 'completed',
        idempotency_key TEXT UNIQUE,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sale_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity REAL NOT NULL,
        unit_price REAL NOT NULL,
        discount_pct REAL DEFAULT 0,
        tax_rate REAL DEFAULT 0,
        line_total REAL NOT NULL,
        FOREIGN KEY (sale_id) REFERENCES sales(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS returns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        return_number TEXT UNIQUE,
        sale_id INTEGER,
        branch_id INTEGER,
        cashier TEXT DEFAULT 'POS',
        reason TEXT,
        refund_method TEXT DEFAULT 'cash',
        refund_amount REAL DEFAULT 0,
        status TEXT DEFAULT 'completed',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (sale_id) REFERENCES sales(id)
    );
    CREATE TABLE IF NOT EXISTS return_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        return_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity REAL NOT NULL,
        unit_price REAL NOT NULL,
        line_total REAL NOT NULL,
        FOREIGN KEY (return_id) REFERENCES returns(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        sale_id INTEGER,
        method TEXT NOT NULL,
        amount REAL NOT NULL,
        reference TEXT,
        status TEXT DEFAULT 'success',
        idempotency_key TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (sale_id) REFERENCES sales(id)
    );
    CREATE TABLE IF NOT EXISTS tax_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        rate REAL NOT NULL,
        is_default INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS journal_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        reference TEXT,
        description TEXT,
        debit_account TEXT,
        credit_account TEXT,
        amount REAL NOT NULL,
        entry_type TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        user_id TEXT,
        action TEXT NOT NULL,
        entity TEXT,
        entity_id INTEGER,
        details TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    -- Multi-device sync foundation (2026-08-06): sync_outbox holds locally
    -- committed writes not yet relayed to the Owner; sync_cursor is a
    -- single-row (id=1) high-water-mark of the last Owner-side seq this
    -- device has pulled. Consumed by Task 4 (routes write to sync_outbox)
    -- and Task 5 (sync client reads/writes both).
    CREATE TABLE IF NOT EXISTS sync_outbox (
        id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sync_cursor (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        last_seq INTEGER NOT NULL DEFAULT 0
    );
    INSERT OR IGNORE INTO sync_cursor (id, last_seq) VALUES (1, 0);
    -- PO-preview-by-supplier foundation (schema v6): a supplier can have
    -- several named contacts (orders/accounts/general), each with its own
    -- preferred channel -- read by core/retail/po_split.py's contact
    -- resolution ladder. See _migrate_add_supplier_contacts_and_po_split
    -- below -- included here too so a brand-new install gets v6 shape
    -- directly without ever running that migration, same as sync_outbox/
    -- sync_cursor just above.
    CREATE TABLE IF NOT EXISTS supplier_contacts (
        id TEXT PRIMARY KEY,
        company_id TEXT,
        supplier_id TEXT NOT NULL REFERENCES suppliers(id),
        name TEXT NOT NULL,
        role TEXT DEFAULT 'orders',
        email TEXT,
        phone TEXT,
        whatsapp TEXT,
        channel_preference TEXT DEFAULT 'whatsapp',
        is_primary INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_supplier_contacts_supplier
        ON supplier_contacts(company_id, supplier_id, status);
    """)
    # idx_po_split_group / idx_po_idempotency are deliberately NOT created
    # here even though a brand-new install already has the columns (line
    # ~1020 above): this executescript runs BEFORE ensure_schema_version()
    # below, so on any pre-v6 install whose purchase_orders table predates
    # split_group_id/idempotency_key, an unconditional CREATE INDEX on those
    # columns crashes init_retail() before the version-gated migration ever
    # gets a chance to ALTER them in. _migrate_add_supplier_contacts_and_po_split
    # already creates both indexes safely (column-existence-checked ALTER,
    # then CREATE INDEX IF NOT EXISTS) for both fresh and legacy installs --
    # see ensure_schema_version() call below. Found via a real desktop .exe
    # launch against a genuine pre-v6 AppData database, not a synthetic test.
    conn.commit()

    # Wave 1B (Part K) / multi-device sync foundation (2026-08-06): first
    # real Retail schema changes -- see _migrate_retail_schema above, which
    # chains the categories/products/customers/suppliers UUID migrations, the
    # products.supplier_id and PO-split additions, and finally the einvoice_*
    # tables (docs/einvoicing/phase1/) that reached this file from master's
    # own, superseded v2/v3 numbering (see the RETAIL_SCHEMA_VERSION comment
    # above for the full merge-reconciliation story).
    # Mirrors products/clinic/backend/database/schema.py's identical
    # ensure_schema_version pattern; see
    # commercial_runtime/security/migration_safety.py.
    from commercial_runtime.security.migration_safety import ensure_schema_version
    ensure_schema_version(
        conn, _get_path('retail'), RETAIL_SCHEMA_VERSION, _migrate_retail_schema,
        backup_dir=os.path.join(BASE_DIR, 'migration_backups'),
    )

    if cur.execute("SELECT COUNT(*) FROM branches").fetchone()[0] == 0 and not _is_standalone():
        _seed_retail(conn, cur)
    # No conn.close() here -- init_retail()'s finally owns that now, so it
    # runs on the exceptional path too. See its docstring.


def _seed_retail(conn, cur, company_id=1):
    """Seed demo data for ONE company. `company_id` defaults to 1 to preserve
    the existing dev-mode first-boot behaviour. Every INSERT is parameterized
    on company_id -- callers that need to reset a specific company's demo
    data (see api/retail_api.py::demo_seed) must pass company_id explicitly."""
    now = datetime.now()
    cid = company_id

    cur.executemany("INSERT INTO branches (company_id,name,address,phone) VALUES (?,?,?,?)", [
        (cid, 'Main Branch', '100 Market Street, Downtown', '+1-555-1001'),
        (cid, 'North Branch', '45 Commerce Ave, Northside', '+1-555-1002'),
        (cid, 'West Mall', '200 Shopping Blvd, Westgate', '+1-555-1003'),
    ])

    # categories.id is TEXT (client-generated UUID) since the multi-device
    # sync foundation migration (see _migrate_categories_to_uuid) -- there is
    # no autoincrement to fall back on, so seed data must generate its own
    # ids explicitly, same as any real device would.
    import uuid as _uuid
    category_names = ['Electronics', 'Clothing', 'Food & Beverages', 'Home & Living', 'Health & Beauty']
    category_ids = [str(_uuid.uuid4()) for _ in category_names]
    cur.executemany("INSERT INTO categories (id,company_id,name) VALUES (?,?,?)", [
        (category_ids[i], cid, category_names[i]) for i in range(len(category_names))
    ])

    # suppliers.id is TEXT (client-generated UUID) since the multi-device
    # sync foundation migration (see _migrate_suppliers_to_uuid) -- there is
    # no autoincrement to fall back on, so seed data must generate its own
    # ids explicitly, same as categories/products/customers above.
    supplier_ids = [str(_uuid.uuid4()) for _ in range(3)]
    cur.executemany("INSERT INTO suppliers (id,company_id,name,phone,email) VALUES (?,?,?,?,?)", [
        (supplier_ids[0], cid, 'TechDistrib Ltd', '+1-555-9001', 'orders@techdistrib.com'),
        (supplier_ids[1], cid, 'FashionWholesale Co', '+1-555-9002', 'supply@fashionwholesale.com'),
        (supplier_ids[2], cid, 'GroceryDirect', '+1-555-9003', 'bulk@grocerydirect.com'),
    ])

    cur.execute("INSERT INTO tax_rates (company_id,name,rate,is_default) VALUES (?,'Standard VAT',15,1)", (cid,))
    cur.execute("INSERT INTO tax_rates (company_id,name,rate,is_default) VALUES (?,'Zero Rated',0,0)", (cid,))
    cur.execute("INSERT INTO tax_rates (company_id,name,rate,is_default) VALUES (?,'Reduced Rate',5,0)", (cid,))

    # 4th element is a 1-based index into category_names/category_ids above
    # (1=Electronics .. 5=Health & Beauty), preserved from the source data's
    # positional convention -- resolved to the real generated UUID below,
    # since category_id is no longer a small autoincrement int.
    products = [
        ('SKU-R001', '6001001', 'Laptop 15" Pro', 1, 750.0, 1199.99, 15, 'pcs', 3),
        ('SKU-R002', '6001002', 'Wireless Earbuds', 1, 25.0, 59.99, 15, 'pcs', 10),
        ('SKU-R003', '6001003', 'Smart Watch', 1, 85.0, 199.99, 15, 'pcs', 5),
        ('SKU-R004', '6001004', 'USB-C Charger', 1, 8.0, 24.99, 15, 'pcs', 15),
        ('SKU-R005', '6001005', 'Classic T-Shirt', 2, 4.5, 14.99, 0, 'pcs', 20),
        ('SKU-R006', '6001006', 'Denim Jeans', 2, 18.0, 49.99, 0, 'pcs', 10),
        ('SKU-R007', '6001007', 'Running Shoes', 2, 35.0, 89.99, 0, 'pcs', 8),
        ('SKU-R008', '6001008', 'Mineral Water 1L', 3, 0.3, 1.49, 0, 'pcs', 50),
        ('SKU-R009', '6001009', 'Orange Juice 1L', 3, 0.8, 2.99, 0, 'pcs', 40),
        ('SKU-R010', '6001010', 'Coffee Blend 500g', 3, 5.0, 12.99, 0, 'pcs', 25),
        ('SKU-R011', '6001011', 'LED Desk Lamp', 4, 12.0, 34.99, 15, 'pcs', 8),
        ('SKU-R012', '6001012', 'Shampoo 400ml', 5, 2.5, 7.99, 5, 'pcs', 20),
    ]
    # products.id is TEXT (client-generated UUID) since the multi-device sync
    # foundation migration (see _migrate_products_to_uuid) -- there is no
    # autoincrement to fall back on, so seed data must generate its own ids
    # explicitly, same as any real device would (mirrors the categories fix
    # above; `_uuid` is already imported there, reused here).
    product_ids = [str(_uuid.uuid4()) for _ in products]
    for i, p in enumerate(products):
        cat_id = category_ids[p[3] - 1]
        row = (p[0], p[1], p[2], cat_id) + p[4:]
        cur.execute(
            "INSERT INTO products (id,company_id,sku,barcode,name,category_id,cost_price,sell_price,tax_rate,unit,reorder_level) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (product_ids[i], cid) + row,
        )

    cur.execute("SELECT id FROM products WHERE company_id=?", (cid,))
    prod_ids = [r[0] for r in cur.fetchall()]
    for pid in prod_ids:
        qty = random.randint(10, 150)
        cur.execute(
            "INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,1,?)",
            (cid, pid, qty)
        )
        cur.execute(
            "INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,2,?)",
            (cid, pid, random.randint(5, 60))
        )

    customers = [
        ('Ahmed Al-Mansouri', '+1-555-2001', 'ahmed@email.com', 450, 3200.0),
        ('Sara Johnson', '+1-555-2002', 'sara@email.com', 120, 890.0),
        ('Michael Chen', '+1-555-2003', 'mchen@email.com', 800, 6500.0),
        ('Fatima Al-Hassan', '+1-555-2004', 'fatima@email.com', 60, 340.0),
        ('Carlos Rivera', '+1-555-2005', 'carlos@email.com', 290, 2100.0),
    ]
    # customers.id is TEXT (client-generated UUID) since the multi-device
    # sync foundation migration (see _migrate_customers_to_uuid) -- there is
    # no autoincrement to fall back on, so seed data must generate its own
    # ids explicitly, same as categories/products above.
    customer_ids = [str(_uuid.uuid4()) for _ in customers]
    for i, c in enumerate(customers):
        cur.execute(
            "INSERT INTO customers (id,company_id,name,phone,email,loyalty_points,total_spent) VALUES (?,?,?,?,?,?,?)",
            (customer_ids[i], cid) + c,
        )

    from core.retail import pricing as _tax_engine
    try:
        _row = cur.execute(
            "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='tax_calculation_mode'", (cid,)
        ).fetchone()
        tax_mode = _tax_engine.normalize_mode(_row[0] if _row else None)
    except Exception:
        tax_mode = _tax_engine.DEFAULT_MODE  # retail_settings not created yet (fresh install) -- use the default
    sale_num = 1000
    methods = ['cash', 'cash', 'card', 'card', 'digital_wallet']
    for i in range(40):
        d = (now - timedelta(days=random.randint(0, 30))).strftime('%Y-%m-%d %H:%M:%S')
        branch_id = random.choice([1, 2])
        # customer_ids holds real generated UUIDs (customers.id is TEXT now,
        # see _migrate_customers_to_uuid) -- picking from it, not a
        # hardcoded 1-5 range, mirrors the cat_id fix above for the same
        # reason: a stale small-int id would never match any real customer
        # row via `s.customer_id=c.id`, silently showing every demo sale as
        # "Walk-in".
        cust_id = random.choice(customer_ids + [None])
        method = random.choice(methods)
        num_items = random.randint(1, 4)
        subtotal = 0
        tax_total = 0
        sale_num += 1
        sn = f'S-{sale_num}'
        cur.execute(
            "INSERT INTO sales (company_id,sale_number,branch_id,customer_id,cashier,payment_method,status,created_at) VALUES (?,?,?,?,?,?,'completed',?)",
            (cid, sn, branch_id, cust_id, 'Cashier', method, d)
        )
        sid = cur.lastrowid
        for _ in range(num_items):
            pid = random.choice(prod_ids)
            row = cur.execute("SELECT sell_price,tax_rate FROM products WHERE id=?", (pid,)).fetchone()
            price, trate = row[0], row[1]
            qty = random.randint(1, 3)
            disc = random.choice([0, 0, 0, 5, 10])
            _calc = _tax_engine.calculate_line(price, qty, discount_pct=disc, tax_rate=trate, mode=tax_mode)
            line = round(_calc['gross'] - _calc['discount_amount'], 2)
            tax = _calc['tax']
            subtotal += line
            tax_total += tax
            cur.execute(
                "INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,discount_pct,tax_rate,line_total) VALUES (?,?,?,?,?,?,?)",
                (sid, pid, qty, price, disc, trate, line)
            )
            cur.execute(
                "INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,unit_cost,reference,created_by) VALUES (?,?,?,?,?,?,?,?)",
                (cid, pid, branch_id, 'sale_out', -qty, price * 0.6, sn, 'System')
            )
        total = round(subtotal + tax_total, 2)
        cur.execute(
            "UPDATE sales SET subtotal=?,tax_amount=?,total=?,amount_paid=?,change_amount=0 WHERE id=?",
            (round(subtotal, 2), round(tax_total, 2), total, total, sid)
        )

    # ── the demo shop's opening declaration (Phase 3, schema v15) ───────────
    #
    # Everything above writes `inventory_balances` rows out of thin air and
    # then posts `sale_out` movements against them, so the demo database
    # arrived with a cache its own ledger flatly contradicted: 120 on hand
    # for a product whose entire recorded history is "-8". That was a real
    # inconsistency in seed data, not a rounding artefact, and until v15 it
    # merely made `stock_reconciliation.compute_drift` report every seeded
    # product forever. It stopped being harmless at v15 for two reasons:
    #
    #   * v15's gate refuses to advance `PRAGMA user_version` while a
    #     REPAIRABLE balance cannot be reproduced from the ledger. This
    #     seeder runs AFTER the migration on a fresh install, so such a
    #     database is born past the gate and fails it the next time ANY
    #     migration runs -- v16 is already reserved in ROADMAP.md. Every
    #     demo and dev install would have refused to start, on a defect
    #     planted months earlier by seed data.
    #   * the Repair action rewrites a drifted balance from its ledger total.
    #     On these rows that total is NEGATIVE (sales with nothing to sell
    #     from), so repairing a demo database drove its whole catalogue below
    #     zero -- while reporting success.
    #
    # The fix is the honest one and it changes no balance: the demo shop
    # DECLARES the opening stock it must have had, exactly as
    # api/import_api.py records a real operator's opening declaration
    # ('opening_stock' / 'OPENING'). Declared quantity is what is on hand now
    # plus everything sold since, so the ledger sums to the cached balance
    # and the demo database can pass the same gate a customer's does.
    #
    # Computed per (product, branch) from what was actually written rather
    # than from the quantities chosen above, so it stays correct if the sales
    # loop, the branch ids or the balance rows ever change shape.
    residuals = cur.execute(
        "SELECT b.product_id, b.branch_id, "
        "       b.quantity_on_hand - COALESCE(("
        "           SELECT SUM(m.quantity) FROM inventory_movements m "
        "           WHERE m.company_id=b.company_id AND m.product_id=b.product_id "
        "             AND m.branch_id IS b.branch_id), 0) AS opening "
        "FROM inventory_balances b WHERE b.company_id=?",
        (cid,)
    ).fetchall()
    for product_id, branch_id, opening in residuals:
        if not opening:
            continue
        cur.execute(
            "INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,reference,notes,created_by) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (cid, product_id, branch_id, 'opening_stock', float(opening), 'OPENING',
             'Demo opening stock', 'System')
        )

    conn.commit()
