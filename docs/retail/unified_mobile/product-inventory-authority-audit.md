# Aura Retail Unified Mobile — Product/Inventory Authority Audit (M5.5.0)

Real, cited audit of the current Python backend and current Android app, before extending any Product/Inventory behavior. Every claim below cites an exact file:line. Where something does not exist in the real authority, that is stated explicitly — a real gap is as important a finding as a real rule.

## 1. Legacy schema — `products/retail/backend/database/schema.py`

**`products`** (98-113): `id` PK, `company_id` DEFAULT 1, `sku TEXT NOT NULL` (**no UNIQUE constraint in DDL**), `barcode TEXT` (nullable, no UNIQUE), `name TEXT NOT NULL`, `category_id` FK nullable, `cost_price/sell_price/tax_rate REAL`, `unit TEXT DEFAULT 'pcs'`, `reorder_level INTEGER DEFAULT 5`, `status TEXT DEFAULT 'active'`, `created_at TIMESTAMP`. **No `updated_at`, no optimistic-version column anywhere.**

**`categories`** (91-97): no `status`/archive column at all — already known from M1's Product Owner Scope Override (`complete-retail-capability-matrix.md`); the unified schema already added `categories.status` as CANONICAL_UNIFIED.

**`inventory_movements`** (114-127): `product_id NOT NULL` FK (no cascade), `branch_id` nullable no FK, `movement_type TEXT NOT NULL` (free text, no CHECK constraint), `quantity REAL NOT NULL` (**signed** — sale writes negative, everything else positive), `unit_cost`, `reference`, `notes`, `created_by DEFAULT 'System'`, `created_at`.

**`inventory_balances`** (128-137): `product_id NOT NULL`, `branch_id NOT NULL`, `quantity_on_hand REAL DEFAULT 0`, `quantity_reserved REAL DEFAULT 0` — real finding: `quantity_reserved` is declared but **never read or written anywhere** in `retail_api.py`/`import_api.py` — a dead column. `UNIQUE(company_id, product_id, branch_id)` (135) — the real physical-uniqueness rule M5.5.5 must preserve.

## 2. Product CRUD — `products/retail/backend/api/retail_api.py`

| Field/rule | Real behavior | Citation |
|---|---|---|
| `name` required | yes, 400 if missing | 249-250 |
| `sku` required | yes, 400 if missing | 249-250 |
| `sku` uniqueness | app-level `SELECT` before `INSERT`, scoped to `company_id`, 409 on conflict — **race-prone, no `BEGIN IMMEDIATE`** unlike `create_sale` | 253-257 |
| `sku` case sensitivity | case-sensitive (`=`, no `COLLATE NOCASE`) | schema.py:101, retail_api.py:254 |
| `sku` patchable | **no** — not in `update_product`'s whitelist | 295 |
| `barcode` uniqueness | **none at all** — no DB constraint, no app check | 263 (no check exists) |
| `barcode` required | no, defaults to `''` | 263 |
| `category_id` validated | **no** — accepted with zero existence/active check | 224-286 (no check found) |
| price/tax/reorder validation | **none** — `.get(...)` with defaults, negative prices accepted | 258-266 |
| `update_product` existence check | **none** — nonexistent id silently no-ops, still returns success | 288-305 |
| optimistic concurrency | **does not exist** | confirmed absent, whole file |
| get-by-id route | **does not exist** | confirmed by full route grep |
| get-by-barcode/SKU route | **does not exist server-side** | confirmed by full route grep |
| `delete_product` | soft-delete (`status='inactive'`) if `sale_items` reference it, else hard-delete + delete `inventory_balances` — `inventory_movements` rows are **not** cleaned up (orphaned, only survives because FK has no cascade) | 307-326 |
| `list_products` | only `status='active'`, no pagination, no search/filter | 224-240 |

## 3. Stock mutation

Every legacy write path is a **relative delta** paired with an `inventory_movements` insert, in the same transaction, with one real exception:

- `create_product` opening stock: `movement_type='opening_stock'` (271-279).
- `adjust_stock` (manual): `stock_in`/`stock_out`, rejects `qty==0`, **no floor check — can push balance negative** (328-362).
- `receive_purchase_order`: `purchase_in` (573-610).
- `create_sale`: decrements in the same function/transaction, `sale_out`, negative quantity, **oversell blocked** by a pre-write check inside `BEGIN IMMEDIATE` (694-697, 653).
- `create_return`: restores in the same function/transaction, `return_in` (1002-1009).
- **Real gap, not to be silently ported**: `import_api.py`'s `_handle_retail_products` (1084-1093) does an **absolute overwrite** of `quantity_on_hand` for imported rows with `initial_stock`, with **no `inventory_movements` row at all** — breaks the "every stock change is audited" invariant that holds everywhere else in the legacy system. Classified `DEPRECATED` for the unified importer (Milestone 18 must not replicate this gap).
- **No stock-take/reconcile-to-counted-quantity feature exists** — `adjust_stock` only ever accepts a signed delta.

## 4. Barcode handling

Always TEXT, never cast to int/float (schema.py:102, retail_api.py:263, import_api.py:1065/1077) — leading zeros are safe in the legacy system by construction. No uniqueness enforcement anywhere. Can be null/empty.

**Real cross-layer finding**: Android's client-side `findProductByCode` (`android/aura-retail/.../barcode/ProductLookup.kt:11-15`) matches barcode **and SKU** case-**insensitively** (`ignoreCase = true`), while the backend's SKU uniqueness check is case-**sensitive**. These two are already inconsistent in the shipped legacy product — worth a deliberate M5.5.2 decision, not a silent port of either half alone.

## 5. SKU handling

Required, app-checked, not DB-constrained, case-sensitive, immutable after creation (not patchable). Importer treats `sku` as the natural upsert key (import_api.py:1055-1081).

## 6. Low-stock / reorder

`reorder_level` (INTEGER DEFAULT 5) feeds exactly one consumer: `dashboard_stats`'s `low_stock` **count** —
```sql
... WHERE COALESCE(b.qty,0) <= p.reorder_level AND p.status='active'
```
(retail_api.py:131-136) — total on-hand summed **across all branches** (not per-branch), active products only. **There is no route that lists the actual low-stock products**, only the aggregate count.

## 7. Permissions

No per-role RBAC for product/inventory routes. Two layers: `@mt_login_required` + `@mt_require_subsystem('retail')` (session/subsystem-license gate, `mt_auth.py:203-230,269-304`), and `@require_license_capability(code, ...)` (tenant license-state gate, not user role) with codes `retail.product.create`, `retail.product.update` (also gates delete), `retail.stock.adjust` — none of the three are in the restricted-mode allowlist, so a restricted tenant can read but not write. No "manager can adjust stock but cashier cannot" distinction exists today.

## 8. Backup/restore & import

Products/inventory are part of the whole-`retail.db` file backup (`commercial_runtime/backup/service.py`), no product-specific special-casing. Import upserts by `sku` (`import_api.py:1029-1097`), with the absolute-overwrite stock gap noted in §3.

## 9. Existing Python tests

`retail_financial_authority_test.py` (oversell rejection, exact-decrement, cross-company rejection, idempotent sale replay), `retail_returns_wave0_test.py` (over-return blocked, stock restoration), `wave1c_financial_gate_test.py` (end-to-end financial gate), `retail_capability_guard_test.py` (license-state gating, not RBAC), `retail_import_export_test.py` (SKU-based upsert confirmed by `test_reimporting_same_sku_updates_not_duplicates`), `retail_backup_restore_test.py` (generic whole-DB, not product-specific). **No test covers**: barcode duplicates, category-archive interaction (categories have no archive field), concurrent SKU-creation races, negative stock via `adjust_stock`, or stock-take/reconcile (none exists).

## 10. Android app (Chaquopy/WebView-backed — no native Product domain model)

`net/Models.kt:152-158`'s `Product` DTO mirrors `list_products`'s JSON exactly: `id, sku, name, sell_price, cost_price, tax_rate, total_stock, reorder_level, unit, category_name, barcode` — no `category_id` (only the joined name), no `status` (list already filters to active-only server-side).

## Field-by-field classification

| Field | Classification | Reasoning |
|---|---|---|
| id, companyId, sku, barcode, name, categoryId, costPrice, sellPrice, taxRate, unit, reorderLevel, status/isActive, createdAt | LEGACY_REQUIRED | Direct, faithful port — already in the M4 unified schema |
| categories.status | UNIFIED_REQUIRED | M1 Product Owner Override, already built (M5.3) |
| categoryName on Product reads | DERIVED | Always a live `LEFT JOIN`, never stored (already how `selectActiveProducts`/`selectProductById` work) |
| product_name_at_sale (on sale/return lines) | SNAPSHOT_ONLY | DIFF-03, M4, unaffected by this milestone |
| quantity_reserved (inventory_balances) | DEPRECATED | Confirmed dead in the legacy authority (§1) — **not ported**, no column added; M5.5.5's canonical inventory record omits it |
| updated_at (products) | NEW_COMPLETE_PRODUCT_REQUIREMENT | Does not exist in legacy; needed to make optimistic-concurrency conflict messages meaningful (M5.5.3) |
| optimistic version | NEW_COMPLETE_PRODUCT_REQUIREMENT | Does not exist in legacy (real gap, §2) — required by M5.5.3's stale-update rule; smallest viable form is `updated_at`-as-version (see product-domain-contract.md) rather than a new counter column, since it needs no schema addition |
| barcode/SKU uniqueness | NEW_COMPLETE_PRODUCT_REQUIREMENT | Legacy has none (§2/§4) — a real, proven gap for a POS scan-lookup flow (which the legacy system never had server-side either); M5.5.2 defines the unified rule |
| get-by-barcode/get-by-SKU repository methods | NEW_COMPLETE_PRODUCT_REQUIREMENT | Legacy has no server-side route at all (client did all matching, §4) — already built at the repository layer in M5.1 (`getByBarcode`/`getBySku`) |
| stock-take/reconcile-to-counted-quantity | NEW_COMPLETE_PRODUCT_REQUIREMENT | Confirmed absent from the legacy authority (§3/§6) — a real, named requirement in M5.5.7 |
| low-stock **list** (not just count) | NEW_COMPLETE_PRODUCT_REQUIREMENT | Legacy only ever produces a count (§6) — M5.5.12 |
| inventory_movements as an audited, append-only ledger for every mutation | UNIFIED_REQUIRED | Legacy is *almost* there (§3) except the one documented import-path gap, which is explicitly not ported |
| import absolute-overwrite-with-no-movement-row stock path | DEPRECATED | Real, documented legacy defect (§3) — not replicated |

No field was invented merely because other POS products have it — every `NEW_COMPLETE_PRODUCT_REQUIREMENT` row above traces to a specific, cited legacy gap this milestone's own spec explicitly names (stock-take, barcode uniqueness, optimistic concurrency, low-stock listing).
