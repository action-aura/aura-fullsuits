# Retail Catalog & Party Sync Expansion — Design Spec

**Status:** approved by product owner 2026-08-07, proceeding straight to implementation planning per explicit instruction (spec-review-before-plan gate waived by request).

## Goal

Extend multi-device sync (currently Category only, shipped and live-tested) to
**Products, Customers, Suppliers** across desktop, Aura POS (shares desktop's
Python via Chaquopy), and the KMP mobile app — so a full local-network demo
can show catalog and party data staying live-synced across every device on a
license, entirely offline-capable (no cloud dependency; VPS/cloud deployment
[M2] is explicitly paused, so this only needs to work over a closed LAN).

**Branches is explicitly out of scope** — investigation found it dormant: no
FK anywhere references it, no delete/edit route, no frontend CRUD UI, POS
checkout hardcodes `branch_id: 1`, only one seeded row exists. There is
nothing real to sync yet; multi-location becomes its own future feature.

## Why this is bigger than "repeat the Category pattern three times"

Category's migration is the proven template (id INTEGER → TEXT UUID,
rename-swap table rebuild, outbox wiring on create/update/delete), but
investigation surfaced real differences per entity that change the actual
work:

- **Products** has 5 tables with a declared `FOREIGN KEY ... REFERENCES
  products(id)` (`inventory_movements`, `inventory_balances`, `sale_items`,
  `purchase_order_items`, `return_items`) — Category only ever had one
  (`products.category_id`). Every one of these FKs, plus `products` itself,
  must be rebuilt in the same transaction (SQLite auto-rewrites a FK's
  target text when the table it points at is renamed away — the existing
  `_migrate_categories_to_uuid`/`_migrate_products_category_fk_...`
  functions already document and solve this; the same discipline applies
  here, at 5x the table count).
- **~18 existing test-fixture call sites** across 10 files in
  `products/retail/tests/` raw-insert into `products` relying on
  AUTOINCLEMENT-assigned integer ids (e.g. `retail_pricing_test.py:169`,
  `retail_security_test.py:425`, `wave1c_financial_gate_test.py:90`) — every
  one breaks once `products.id` has no default and needs an explicit id.
  Category's own migration had exactly 2 such regressions (`import_api.py`);
  Products has an order of magnitude more surface, because far more tests
  touch products than ever touched categories directly.
- **Customers/Suppliers have no delete route or UI at all today** — this
  isn't a sync gap, it's a pre-existing product gap this plan also closes
  (decided: build it now, soft-delete, while the pattern is fresh).
- **The KMP mobile schema has diverged from a faithful port.**
  `Catalog.sq`'s `products` table added `normalized_name`, `updated_at`
  with optimistic-concurrency `WHERE updated_at = ?` on `updateProduct`,
  case-insensitive unique indexes on sku/barcode, and a whole
  `import_conflicts` table (`canonical_product_id INTEGER`) used by
  `CatalogImporter` for legacy-data migration — none of which desktop's
  Python schema has. Migrating KMP's `products.id` to `String` touches all
  of this, not just a column type. `Parties.sq` (customers/suppliers), by
  contrast, is a faithful, low-divergence port — that side stays close to
  Category's original difficulty.
- **`payments.party_id`** is a polymorphic loose column (`party_type` +
  `party_id`, added at runtime by `_ensure_credit_schema`'s `ALTER TABLE`,
  never a declared FK) referencing either a customer or a supplier id
  depending on `party_type`. Remapping old integer ids to new UUIDs after
  migration must filter on `party_type`, unlike every other remap in this
  plan or in Category's.

None of this blocks the work — it just means "Products" is the single
riskiest task in this plan (comparable in size to the entire Category
migration was), and KMP Products is the riskiest client-side task. Sizing
and task boundaries below reflect that; this is not a 3x repeat of a solved
problem, it's three related but differently-sized migrations.

## Delete semantics (decided)

A synced delete is **always a soft-delete** (`status='inactive'`), never a
real `DELETE FROM` — for all three entities, decided explicitly to avoid the
FK-wedge bug class Category's own delete already went through one fix-round
for (see `_migrate_products_category_fk_on_delete_set_null`'s docstring).
Because delete never removes a row, **no `ON DELETE` clause changes are
needed anywhere this plan touches** — not on products' 5 referencing
tables, not on `purchase_orders.supplier_id` (the one other declared FK in
scope). `sales.customer_id` and `payments.party_id` aren't declared FKs at
all (loose columns, see below) so there's no constraint to change there
either — this is a deliberate simplification versus Category's precedent,
not an oversight.

- `products` and `suppliers` already have `status TEXT DEFAULT 'active'` —
  no new column.
- `customers` needs a new `status TEXT DEFAULT 'active'` column (does not
  exist today).
- `delete_product` (`retail_api.py:396-415`) currently has *conditional*
  behavior (soft-delete if it has sale history, hard-delete otherwise) —
  this plan simplifies it to **always** soft-delete. Two devices on one
  license can legitimately have different sales history for the same
  product (sales don't sync this phase), so a hard delete on one device
  relayed to another could still hit the same class of bug soft-delete-only
  eliminates outright. This is a real, deliberate behavior change to the
  existing route, not just new sync wiring bolted on.
- New `delete_customer`/`delete_supplier` routes: soft-delete only, no
  hard-delete branch (nothing to preserve — these routes don't exist yet).

## Scope: what syncs, what doesn't

**In scope (synced), this plan:**
- Products: create, update, delete (→ soft-delete) — desktop, Aura POS
  (shared Python), KMP.
- Customers: create, update, delete (→ soft-delete, new route) — same three
  clients.
- Suppliers: create, update, delete (→ soft-delete, new route) — same three
  clients.

**Explicitly not in scope:**
- Branches (dormant, see above).
- Sales, Inventory, Returns, Payments, Purchase Orders — these reference
  products/customers/suppliers by id but are not themselves synced this
  round. This means, same as Category's own precedent with products: two
  devices can legitimately hold different sales/inventory history against
  the same synced product/customer/supplier. The soft-delete-only decision
  above is what makes that safe.
- WhatsApp (M7/M8) — paused, needs M2's public webhook.
- Cloud/VPS deployment (M2) — paused per explicit instruction; this plan
  targets closed-LAN demo readiness only, reusing the exact same
  Owner-relay sync mechanism Category already proved over USB — Owner
  itself does not change (`entity_type` is unvalidated free text already,
  confirmed in `owner/app/sync/routes.py`'s `_build_event`, so no Owner-side
  code changes at all are needed for this plan).

## Schema migration strategy (desktop/Aura POS, shared Python)

One schema version bump, `RETAIL_SCHEMA_VERSION` 3 → 4, adding three new
migration functions to `_migrate_retail_schema`'s dispatch chain (mirrors
how v3 added its own step alongside v2's), each independently idempotent
per the existing convention:

1. `_migrate_products_to_uuid` — rebuild `products` (TEXT id) *and* all 5
   referencing tables (`inventory_movements`, `inventory_balances`,
   `sale_items`, `purchase_order_items`, `return_items`) in one transaction,
   `PRAGMA foreign_keys=OFF` around it exactly like the existing two
   migrations. Build an `old_int_id -> new_uuid` map first, rebuild
   `products_new`, then remap every referencing table's `product_id`
   column using that map (same `UPDATE ... WHERE id IN (SELECT id FROM
   <table> WHERE product_id=?)` per-distinct-value pattern
   `_migrate_categories_to_uuid` already uses for `products.category_id`).
   None of the 5 referencing tables' own ids change — only the
   `product_id` values they hold. None of their FK declarations need an
   `ON DELETE` change (delete is soft, see above) — they can be rebuilt
   with the exact same bare `REFERENCES products(id)` they have today.
2. `_migrate_customers_to_uuid` — rebuild `customers` (TEXT id, plus the new
   `status` column, plus folding in `credit_mode`/`credit_limit`/
   `credit_balance` as first-class columns instead of relying on
   `_ensure_credit_schema`'s runtime `ALTER TABLE` to add them after the
   rebuild — those `addcol` calls stay in place as a no-op safety net for
   any install that runs `_ensure_credit_schema` before this migration
   fires, but the rebuilt table should already have them). Remap
   `sales.customer_id` (loose column, no declared FK, but real values that
   must stay meaningful) and `payments.party_id WHERE party_type='customer'`
   using the id map.
3. `_migrate_suppliers_to_uuid` — rebuild `suppliers` (TEXT id, folding in
   `payment_terms`/`credit_balance` as first-class columns the same way).
   Remap `purchase_orders.supplier_id` (this one *is* a declared FK — same
   rename-away hazard as products, `suppliers` itself must never be renamed
   away, only rebuilt via `suppliers_new` swap) and
   `payments.party_id WHERE party_type='supplier'`.

Seed data (`_seed_retail`, `schema.py:576+`) needs the same fix already
applied to categories at line 590-599: generate ids client-side
(`str(uuid.uuid4())`) before insert, for products, customers, and suppliers,
mirroring the existing category seed pattern exactly.

## Backend routes (`retail_api.py`)

- Products: wire `_queue_sync_event` into `create_product` (:331-375) and
  `update_product` (:377-394); simplify `delete_product` (:396-415) to
  always soft-delete + queue delete event; route params become
  `<string:pid>`.
- Customers: wire outbox into `create_customer` (:479-495) and
  `update_customer` (:497-513); **new** `delete_customer` route
  (soft-delete, mirrors `delete_category`'s try/except containment
  pattern — 409 on constraint failure, always closes the connection).
- Suppliers: same as Customers — wire `create_supplier` (:549-565)/
  `update_supplier` (:567-583), **new** `delete_supplier` route.
- `import_api.py`: fix `_handle_retail_products`'s `pid = cur.lastrowid`
  (:1083) the same way its own `cat_id` generation already does two lines
  above it (:1050) — generate the UUID before insert, include it in the
  column list. `_handle_retail_customers`/`_handle_retail_suppliers`
  (:1102-1146) don't capture a returned id today, but their `INSERT`
  statements omit the `id` column entirely — both need an explicit
  generated id added to the column list and values, or the insert fails
  outright once there's no default to fall back on.
- Every raw `INSERT INTO products (...)` test fixture across
  `products/retail/tests/` that omits `id` needs the same explicit-id fix
  — this is inherent to landing the Products migration safely, not a
  follow-up; the implementing task must run the full retail suite and fix
  what it finds, the same discipline the original Category task used.

## Sync apply-side (`commercial_runtime/sync/sync_service.py`)

`_apply_event` gains three new entity-type branches (`product`, `customer`,
`supplier`) alongside the existing `category` branch. Create/update are a
plain upsert (mirrors `category`'s). Delete is **always**
`UPDATE <table> SET status='inactive' WHERE id=?` — never a `DELETE FROM` —
for all three, per the decided delete semantics above. `local_company_id_from_registry`
stamping (the company_id fix from the last plan's live-testing round)
applies identically here — the payload's `company_id` is never trusted,
every apply stamps the receiving device's own.

## KMP mobile (`mobile/aura-retail-unified`)

- **Customers/Suppliers: dropped from KMP this round** — see Residual/
  deferred items below. Desktop and Aura POS still sync both fully.
- **Products: also dropped from KMP this round** (discovered during
  implementation, not during planning — see Residual/deferred items below).
  `products.id` is a live FK target of 5 more KMP schema files
  (`Inventory.sq`, `Sales.sq`, `Returns.sq`, `Purchasing.sq`, `Reporting.sq`)
  this spec never scoped, unlike Category whose only referencing column was
  already `TEXT`. Desktop and Aura POS still sync Products fully.

## Testing strategy

Same discipline as the sync-foundation plan: implementer runs the covering
test file(s) after each change, task reviewer gets a real diff package, one
comprehensive fix wave at the end if the final whole-branch review finds
anything. Given Products' outsized blast radius, its task brief must
explicitly call out running the **full** `products/retail/tests/` suite
(per-file, matching the known pytest cross-file-contamination workaround
already documented from the last plan) rather than just the files an
implementer might guess are related.

New coverage this plan needs, beyond "does sync work": a live LAN
verification pass (desktop + Aura POS on the same network, real device-to-
device create/edit/delete of a product, a customer, and a supplier, matching
how Category was verified over USB) — this is the actual deliverable the
demo depends on, not just unit tests passing.

## Residual/deferred items (documented, not built this round)

- Branches: no real feature exists, deferred entirely.
- KMP Customers/Suppliers sync: dropped during planning (2026-08-07) — unlike
  KMP's own Products (real `ProductUseCases.kt` + repository + UI) and unlike
  desktop's Customers/Suppliers (full CRUD UI), KMP has no Customer/Supplier
  feature at all; `Parties.sq`'s insert/select queries are only ever called
  by `ImportCommitExecutor.kt` (legacy-data import), never by any app
  screen. Wiring sync onto a feature with no UI would be invisible and
  unverifiable through the app itself — same shape as the Branches decision
  above. Desktop and Aura POS still get full Customers/Suppliers sync.
- KMP Products sync: dropped during Task 5's implementation (2026-08-08) —
  the implementer correctly stopped rather than guess. `products.id` is a
  live FK target of 5 more KMP schema files this spec never read
  (`Inventory.sq`, `Sales.sq`, `Returns.sq`, `Purchasing.sq`, `Reporting.sq`)
  plus their repositories (including the concurrency-hardened
  `SqlDelightInventoryRepository.kt`) and a second, separate importer
  (`ImportCommitExecutor.kt`) with its own product-creation path — unlike
  Category, whose only referencing column (`products.category_id`) was
  already `TEXT` before Category's own migration. A correct migration is
  comparable in size to desktop's entire Products work (Tasks 1+2 combined).
  Full findings in the plan's Task 5 section and
  `.superpowers/sdd/2026-08-07-retail-catalog-party-sync-expansion/task-5-report.md`.
  Desktop and Aura POS still get full Products sync. Revisit as its own
  future plan, starting from the full FK graph above.
- Sales/Inventory/Returns/Payments sync: explicitly deferred (this is the
  "high-risk entities" work already flagged in the wider roadmap as needing
  its own design pass for offline-concurrent-stock and no-double-refund
  correctness).
- WhatsApp M7/M8: paused, blocked on M2.
- KMP's `company_id` sync-apply gap (every KMP call site currently
  hardcodes `companyId = 1L`, confirmed inert in the prior plan's review) —
  unchanged by this plan; still worth fixing before KMP sync is relied on
  for anything beyond single-company demo use.
