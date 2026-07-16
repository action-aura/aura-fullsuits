# Aura Retail — Data Integrity Audit

Evidence gathered by direct source read (`products/retail/backend/database/schema.py`,
`products/retail/backend/api/retail_api.py`). All line numbers cited are PROVEN
(read directly), not inferred.

## Schema

16 tables (`products/retail/backend/database/schema.py` lines 68-272), plus 3
lazily-created tables (`payment_methods`, `retail_settings`, `doc_sequences` —
created on first use by `retail_api.py::_ensure_credit_schema`, not in the main
schema function). All `id INTEGER PRIMARY KEY AUTOINCREMENT`, all
`CREATE TABLE IF NOT EXISTS`.

**Foreign keys are declared on several tables** (`products.category_id`,
`inventory_movements.product_id`, `inventory_balances.product_id`,
`purchase_orders.supplier_id`, `purchase_order_items.po_id/product_id`,
`sale_items.sale_id/product_id`, `returns.sale_id`, `return_items.return_id/product_id`,
`payments.sale_id`) **but `branch_id` is never declared as a FK anywhere despite
being used as one throughout**, and `sales.customer_id`/`sales.branch_id` are bare
columns with no FK declaration at all.

**Finding — FK enforcement is OFF for every Retail connection.**
`schema.py`'s `_conn()` sets `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=30000`
but **never sets `PRAGMA foreign_keys=ON`**, and this is the only connection
factory in the file (confirmed no other `get_retail_conn`/`_conn` variant exists).
SQLite defaults foreign-key enforcement to OFF. **Every `FOREIGN KEY ... REFERENCES`
clause in Retail's schema is therefore decorative — declared but never checked by
the database engine.** A row can reference a nonexistent `product_id`/`sale_id`
and SQLite will not reject the write. This directly contradicts Clinic's identical
schema pattern, which **does** set `PRAGMA foreign_keys=ON` (see `07`) — a real,
proven cross-product inconsistency, and a real integrity gap for Retail (orphan
rows are structurally possible, not just theoretically).

No indexes beyond the implicit ones from `UNIQUE`/`PRIMARY KEY` constraints exist
in the main schema; one `CREATE INDEX idx_payments_reference` is added lazily by
`_ensure_credit_schema`.

## Migrations

No `schema_version` table, no version-gated migration framework. Schema evolution
happens via `_ensure_credit_schema()`'s `addcol()` helper (`retail_api.py`
lines 993-1037), which runs `PRAGMA table_info` to check if a column already
exists before running `ALTER TABLE ... ADD COLUMN`, wrapped in a bare
`try/except Exception: pass` with **no distinction between "column already
exists" (expected, harmless) and any other failure (e.g. disk full, locked
database) — both are silently swallowed**, and execution proceeds assuming the
column now exists. This function is gated to run once per process
(`_CREDIT_SCHEMA_READY` module-level flag) and is called defensively at the top
of ~20 different routes (customers/suppliers/payments/purchase-order routes).
**There is no rollback or downgrade path of any kind** — this is an
additive-only, best-effort, in-place migration strategy with no version tracking
and no failure visibility beyond a silently-swallowed exception.

## Soft delete vs. hard delete

- `delete_product`: conditional — soft-deletes (`status='inactive'`) if the
  product has any `sale_items` history, hard-deletes (`inventory_balances` then
  `products` rows) otherwise. **The existence check
  (`SELECT COUNT(*) FROM sale_items WHERE product_id=?`) has no `company_id`
  filter** — a cross-tenant `product_id` collision could cause the wrong
  branch (soft vs. hard) to be chosen, though the actual mutating statements
  immediately after are correctly company-scoped, so this is a logic gap, not a
  cross-tenant write.
- `void_payment`: soft only (`status='voided'`), no hard delete path.
- Demo-wipe: 16 real `DELETE FROM` statements, every one `company_id`-scoped
  (directly or via a company-scoped parent subquery for child tables that lack
  their own `company_id` column), gated behind demo-mode-only + admin-role +
  explicit `WIPE-<company_id>` confirmation-token checks.

## Multi-tenant isolation

15+ sampled write/read sites across `retail_api.py` are correctly `company_id`-scoped.
Three non-exploitable gaps found (each relies on an already-validated upstream
value, not fresh user input, so not a live IDOR):
`delete_product`'s existence-count query (above), `receive_purchase_order`'s
child-row updates (rely on a parent row already fetched with a `company_id`
filter), `void_payment`'s final `UPDATE` (relies on a payment row already fetched
scoped). **No live cross-tenant read/write vulnerability was found in Retail** in
this pass — worth stating plainly since Clinic's equivalent audit (`07`) found and
documents a real, already-fixed historical one. `retail_api.py` has exactly one
commit in the repo's history (the original extraction commit) — this isolation
posture was correct from the moment the file was extracted, not patched later.

## Transaction boundaries

- `create_sale`: explicit `BEGIN TRANSACTION`, explicit `rollback()` on every
  early-return path and in the `except` block. **Correctly atomic.**
- `create_return`: **no explicit `BEGIN TRANSACTION`** (relies on sqlite3's
  implicit transaction), but does have a `try/except` with `rollback()` on
  failure — a mid-function crash is still rolled back, just via the implicit
  mechanism rather than an explicit one. Functionally safe, stylistically
  inconsistent with `create_sale`.
- `receive_purchase_order`: **no `try/except`, no explicit `BEGIN`, no rollback
  path at all.** A sequence of INSERT/UPDATE statements followed by one
  `conn.commit()`. If an exception is raised partway through (e.g. a malformed
  `unit_cost` value in one line of a multi-line PO), it propagates uncaught to
  Flask's default handler, and whatever was written before the exception remains
  in an uncommitted, un-rolled-back state until the connection object is
  eventually garbage-collected. **P2 — a real partial-write risk on a
  multi-item purchase-order receipt, e.g. a crash after item 3 of 5 leaves items
  1-3's inventory increments applied with no corresponding, complete
  purchase-order record, and no automatic cleanup.**
- `_ensure_credit_schema`/`addcol`: no transactional atomicity across the whole
  migration; a failure partway through the ~20 `ALTER TABLE` calls leaves some
  columns added and others not, silently (see Migrations above).

## Concurrency / crash-recovery

Not independently force-tested in this pass (killing the process mid-write was
not attempted — time-boxed, and a genuine kill-mid-write test against a real
running server was judged out of scope for a static/read-based audit pass).
Assessed by code inspection only: WAL journal mode is used (better crash-recovery
characteristics than the default rollback-journal mode — a WAL-mode SQLite
database that's killed mid-write recovers cleanly on next open, replaying or
discarding the WAL as appropriate), and `create_sale`'s explicit transaction
boundary means a kill during a sale either fully commits or fully doesn't, at the
SQLite level. `receive_purchase_order`'s missing transaction wrapper (above) is
the one path where a kill mid-write could plausibly leave a genuinely
inconsistent state (partial inventory increments, no PO row), rather than SQLite
itself misbehaving. **UNVERIFIED by an actual process-kill test — PROVEN by code
reading only.**

## Retail data-model relationships audited

products ↔ categories, products ↔ inventory_movements/inventory_balances,
purchase_orders ↔ suppliers ↔ purchase_order_items, sales ↔ sale_items ↔ products,
returns ↔ sales (declared FK) ↔ return_items ↔ products, payments ↔ sales, users
and audit records live in the separate shared `commercial_runtime` registry, not
in Retail's own schema. All relationships above exist and are used consistently
in the routes read; the only structural gap is FK enforcement being off (so these
relationships are conventions, not enforced constraints).

## Backup / restore

**None exists.** Repo-wide grep across `commercial_runtime/`, `products/retail/backend`,
`products/retail/desktop` for backup/restore/export-db/import-db code returned
zero functional matches. No automatic backup, no manual backup command, no
restore procedure. This is elaborated in `18-backup-and-recovery-audit.md`.

## Android

No separate Android data-integrity surface exists — Android stages and runs the
identical `products/retail/backend` Python/SQLite code via Chaquopy; there is no
native Kotlin database layer (`Room`/`SQLiteOpenHelper`) anywhere in
`android/aura-retail/`. Every finding above therefore applies identically to
Retail-on-Android's data layer.
