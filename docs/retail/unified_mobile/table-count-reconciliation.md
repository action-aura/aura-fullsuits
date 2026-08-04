# Aura Retail Unified Mobile — Table-Count Reconciliation (M5.0.A)

## Real, exact evidence (not recounted from memory)

```
$ grep -oP "(?<=CREATE TABLE )(IF NOT EXISTS )?\K\w+" products/retail/backend/database/schema.py | wc -l
17

$ grep -oP "(?<=CREATE TABLE )(IF NOT EXISTS )?\K\w+" products/retail/backend/api/retail_api.py | wc -l
3   (doc_sequences, payment_methods, retail_settings -- inside _ensure_credit_schema())

$ grep -h "^CREATE TABLE" mobile/aura-retail-unified/shared/src/commonMain/sqldelight/com/actionaura/retail/db/*.sq | wc -l
20
```

**17 + 3 = 20. The unified schema has exactly 20 tables. There are zero net-new table shapes beyond a faithful port of what the real Python authority actually creates.**

## Real root cause of the discrepancy

Milestone 1's "17 tables" claim (`android-feature-parity-matrix.md`, `python-financial-authority-map.md`) is accurate **only for `database/schema.py`'s `init_retail()` function** — the module's own docstring lists exactly 17 tables, and that docstring was read and trusted at face value. It did not separately account for `api/retail_api.py`'s `_ensure_credit_schema()`, which lazily creates **three more real tables** (`payment_methods`, `retail_settings`, `doc_sequences`) the first time any credit/settings/payment-method-touching route runs — in a real production deployment, this happens essentially immediately (the very first sale, the very first settings read).

`android-database-audit.md` (Milestone 4) *did* discover and document this lazy-creation mechanism, but its own prose undercounted it: it named only two of the three lazy tables ("Plus two lazily-created tables (`retail_settings`, `doc_sequences`)"), omitting `payment_methods` from that specific sentence even though the actual `.sq` schema (`Payments.sq`) correctly included `payment_methods` as a first-class table all along. That prose undercount is what produced Milestone 4's own commit-message claim of "19 tables" — a real arithmetic error (17 + 2 named lazy tables = 19), not a schema defect. The schema itself was always complete and correct at 20 tables; only the narrative description of it was wrong by one.

## The corrected, honest claim

**Real legacy authority, fully accounted**: 17 tables from `init_retail()` (created on every fresh database) + 3 tables from `_ensure_credit_schema()` (created lazily, but real and load-bearing the moment any real Retail session exercises credit/payment-method/settings functionality — not optional, not dead code) = **20 real tables**.

**Real unified schema**: **20 tables**, one-to-one with the above. Every unified table name has an exact real legacy source. There is no unified table without a legacy counterpart, and no legacy table without a unified counterpart.

## Full table matrix

| Legacy table | Legacy source authority | Unified table | Relationship | Purpose | Migration rule | Row-count validation | FKs | Indexes (unified) | Backup inclusion | Android migration behavior | iOS fresh-install behavior |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `branches` | `schema.py::init_retail()` | `branches` | TRANSFORMED (REAL columns n/a here; timestamp→epoch millis) | Physical/operational locations | Import with preserved ID (M4 `CatalogImporter`) | Row count matches | none (root table) | `company_id` | Yes | Imported, IDs preserved | Empty; self-heals a default branch on first product/sale, mirroring the legacy `_default_branch()` behavior |
| `categories` | `schema.py::init_retail()` | `categories` | TRANSFORMED (adds `status` column -- M1 Product Owner Override, `complete-retail-capability-matrix.md`) | Product grouping | Import with preserved ID; `status` backfilled to `'active'` for every imported row (no legacy data to migrate from -- real gap, not silently invented) | Row count matches | none (root table) | `company_id`; unique `(company_id, name)` (new -- M1 override) | Yes | Imported, IDs preserved, all `status='active'` | Empty |
| `products` | `schema.py::init_retail()` | `products` | TRANSFORMED (`cost_price`/`sell_price`/`tax_rate` REAL→TEXT) | Catalog items | Import with preserved ID; price/tax fields converted per `legacy-real-to-text-conversion-contract.md` | Row count matches; sum of `sell_price` (as Decimal) matches within the conversion contract's tolerance | `category_id → categories(id)` | `company_id`, `category_id`, unique `(company_id, sku)`, `barcode` | Yes | Imported, IDs preserved | Empty |
| `inventory_movements` | `schema.py::init_retail()` | `inventory_movements` | TRANSFORMED (`quantity`/`unit_cost` REAL→TEXT; timestamp→epoch millis) | Stock movement audit trail | Milestone 18 scope (not yet implemented -- `data-preservation-plan.md`'s own honest scope boundary) | Milestone 18 | `product_id → products(id)` | `company_id`, `product_id`, `branch_id` | Yes | Milestone 18 | Empty |
| `inventory_balances` | `schema.py::init_retail()` | `inventory_balances` | TRANSFORMED (`quantity_on_hand`/`quantity_reserved` REAL→TEXT) | Current stock levels | Milestone 18 scope | Milestone 18 | `product_id → products(id)` | `company_id`, `product_id`, unique `(company_id, product_id, branch_id)` | Yes | Milestone 18 | Empty |
| `customers` | `schema.py::init_retail()` | `customers` | TRANSFORMED (`loyalty_points`/`total_spent`/`credit_limit`/`credit_balance` REAL→TEXT; `credit_mode`/`credit_limit`/`credit_balance` -- real ALTER-added columns in the legacy DB -- declared first-class) | Customer/AR records | Milestone 18 scope | Milestone 18 | none (root table) | `company_id` | Yes | Milestone 18 | Empty |
| `suppliers` | `schema.py::init_retail()` | `suppliers` | TRANSFORMED (`credit_balance` REAL→TEXT; `payment_terms`/`credit_balance` real ALTER-added columns declared first-class) | Supplier/AP records | Milestone 18 scope | Milestone 18 | none (root table) | `company_id` | Yes | Milestone 18 | Empty |
| `purchase_orders` | `schema.py::init_retail()` | `purchase_orders` | TRANSFORMED (money columns REAL→TEXT; `amount_paid`/`payment_status`/`due_date` real ALTER-added columns declared first-class; timestamps→epoch millis) | Purchasing | Milestone 18 scope | Milestone 18 | `supplier_id → suppliers(id)` | `company_id`, `supplier_id` | Yes | Milestone 18 | Empty |
| `purchase_order_items` | `schema.py::init_retail()` | `purchase_order_items` | TRANSFORMED (money/qty REAL→TEXT) | PO line items | Milestone 18 scope | Milestone 18 | `po_id → purchase_orders(id)`, `product_id → products(id)` | `po_id`, `product_id` | Yes | Milestone 18 | Empty |
| `sales` | `schema.py::init_retail()` | `sales` | TRANSFORMED (money columns REAL→TEXT; `due_date` real ALTER-added column declared first-class; timestamp→epoch millis) | Finalized sales | Milestone 18 scope | Milestone 18 | `customer_id → customers(id)` | `company_id`, `customer_id`, `branch_id` | Yes | Milestone 18 | Empty |
| `sale_items` | `schema.py::init_retail()` | `sale_items` | TRANSFORMED + gains `product_name_at_sale` (CANONICAL_UNIFIED, DIFF-03) | Sale line items | Milestone 18 scope; `product_name_at_sale` backfilled from the product's CURRENT name at migration time (best-effort, `data-preservation-plan.md`) | Milestone 18 | `sale_id → sales(id)`, `product_id → products(id)` | `sale_id`, `product_id` | Yes | Milestone 18 | Empty |
| `returns` | `schema.py::init_retail()` | `returns` | TRANSFORMED (money REAL→TEXT; `idempotency_key` real ALTER-added column + real partial unique index reproduced verbatim; timestamp→epoch millis) | Returns | Milestone 18 scope | Milestone 18 | `sale_id → sales(id)` | `company_id`, `sale_id`, partial unique `idempotency_key` | Yes | Milestone 18 | Empty |
| `return_items` | `schema.py::init_retail()` | `return_items` | TRANSFORMED + gains `product_name_at_sale` (same as `sale_items`) | Return line items | Milestone 18 scope | Milestone 18 | `return_id → returns(id)`, `product_id → products(id)` | `return_id`, `product_id` | Yes | Milestone 18 | Empty |
| `payments` | `schema.py::init_retail()` | `payments` | TRANSFORMED (money REAL→TEXT; 13 real ALTER-added ledger-extension columns from `_ensure_credit_schema()` declared first-class: `party_type`/`party_id`/`direction`/`currency`/`fx_rate`/`related_type`/`related_id`/`notes`/`reversal_of`/`voided_by`/`voided_at`/`created_by`/`device`) | Unified payment ledger | Milestone 18 scope | Milestone 18 | `sale_id → sales(id)`, `reversal_of → payments(id)` | `company_id`, `sale_id`, `reference`, `(party_type, party_id)` | Yes | Milestone 18 | Empty |
| `tax_rates` | `schema.py::init_retail()` | `tax_rates` | TRANSFORMED (`rate` REAL→TEXT) | Named tax rates | Milestone 18 scope | Milestone 18 | none | `company_id` | Yes | Milestone 18 | Empty |
| `journal_entries` | `schema.py::init_retail()` | `journal_entries` | TRANSFORMED (`amount` REAL→TEXT) | GL-adjacent ledger entries | Milestone 18 scope | Milestone 18 | none | `company_id` | Yes | Milestone 18 | Empty |
| `audit_log` | `schema.py::init_retail()` | `audit_log` | TRANSFORMED (timestamp→epoch millis) | Audit trail | Milestone 18 scope | Milestone 18 | none | `company_id`, `(entity, entity_id)` | Yes | Milestone 18 | Empty |
| `payment_methods` | `retail_api.py::_ensure_credit_schema()` (lazy, real) | `payment_methods` | UNCHANGED SHAPE, declared first-class (no lazy-creation quirk carried forward) | Configured payment method list | Milestone 18 scope | Milestone 18 | none | `company_id` | Yes | Milestone 18 | Empty; seeded with defaults on first run (mirrors legacy `_seed_methods()`, Milestone 5+ scope) |
| `retail_settings` | `retail_api.py::_ensure_credit_schema()` (lazy, real) | `retail_settings` | UNCHANGED SHAPE, declared first-class | Key/value business settings | Milestone 18 scope | Milestone 18 | none (PK is `(company_id, skey)`) | n/a (PK covers lookups) | Yes | Milestone 18 | Empty; defaults resolved in code (mirrors legacy `_DEFAULT_SETTINGS` fallback) |
| `doc_sequences` | `retail_api.py::_ensure_credit_schema()` (lazy, real) | `doc_sequences` | UNCHANGED SHAPE, declared first-class | Document numbering counters | Milestone 18 scope | Milestone 18 | none (PK is `(company_id, doc_type)`) | n/a | Yes | Milestone 18 | Empty; counters start at 0 |

## Corrected claim, going forward

**Do not describe this as "17 legacy tables, 19 unified tables, 2 new."** The correct, real claim is: **20 real legacy tables (17 always-created + 3 lazily-but-really-created) map one-to-one to 20 unified tables.** Zero table shapes were invented. The only real additions are: one new `status` column on `categories` (M1 Product Owner Override, a column not a table), one new `product_name_at_sale` column each on `sale_items`/`return_items` (DIFF-03, a column not a table), and real index coverage (never present in the legacy schema at all). Every prior document referencing "19 tables" (the M4 commit message, `shared-database-schema-decision.md`'s own prose) is superseded by this reconciliation, per this whole engagement's standing discipline of correcting test/count claims additively rather than silently.
