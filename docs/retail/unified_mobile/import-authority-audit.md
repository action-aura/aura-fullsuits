# Import Authority Audit (M5.8.0)

Real, cited audit of `products/retail/backend/api/import_api.py`
(1,186 lines, read in full, not sampled) — the entire real Retail import
authority: routes, cleaning pipeline, entity handlers, all in one file.
Cross-checked against `products/retail/tests/retail_import_export_test.py`
(25 real tests) and `products/retail/backend/database/schema.py`
(confirmed: no import-audit/import-log/import-history table exists
anywhere — provenance is a genuinely new requirement, not a port).

## The real five entity handlers — confirmed by exact name, not assumed

```python
_HANDLERS = {
    ('retail', 'products'):   _handle_retail_products,
    ('retail', 'customers'):  _handle_retail_customers,
    ('retail', 'suppliers'):  _handle_retail_suppliers,
    ('retail', 'branches'):   _handle_retail_branches,
    ('retail', 'categories'): _handle_retail_categories,
}
```

**Not** Sales/Invoice, **not** Inventory as a standalone entity (Product
import carries `initial_stock` as one of its own fields, folded into
`_handle_retail_products` — there is no separate Inventory handler or
schema entity). This confirms the checkpoint's own caution was
warranted: the real list is `products, customers, suppliers, branches,
categories`, not the speculative `Products/Categories/Suppliers/
Sales/Inventory` list.

## Real dependency order (already encoded in the legacy authority)

```python
_ENTITY_ORDER = ['categories', 'suppliers', 'branches', 'customers', 'products']
```

Used by `smart_execute`'s multi-entity import to sort `targets` before
running handlers — categories/suppliers/branches before
customers/products. This is real, existing evidence for M5.8.12's
dependency graph, not something to invent from scratch. Note: `customers`
has no real dependency on anything before it in this order; it is
ordered this way only because it is alphabetically/conventionally placed
before `products`, not because of an FK relationship — the only real FK
dependency is `products.category_id → categories.id`.

## Per-handler audit

### `_handle_retail_products` (line 1029)

- **Accepted fields**: `name*, sku*, barcode, category, cost_price,
  sell_price*, tax_rate, unit, reorder_level, initial_stock` (`*` =
  required per `SCHEMAS['retail']['products']`).
- **Duplicate key**: `(company_id, sku)` exact match → `UPDATE` (never
  skip, never reject) — no `sku` → row silently `skipped` (counted in
  `skipped`, not in `imported`/`updated`).
- **Real gap, disclosed**: no barcode-uniqueness check inside the
  handler at all — two products with different SKUs but the same
  barcode are NOT caught by this endpoint (the underlying `products`
  table may or may not enforce this at the DB layer; the import handler
  itself does not check).
- **Category handling**: auto-creates a `categories` row by exact name
  if it doesn't already exist for this company — **real, existing,
  intentional behavior**, not a bug. `cat_cache` avoids redundant
  lookups within one import call. Category name matching is
  **case-sensitive** exact string match (`WHERE company_id=? AND
  name=?`, no `.lower()`).
- **Branch handling**: auto-creates a `"Main Store"` branch if the
  company has zero branches — real, existing, intentional default.
- **Inventory**: `initial_stock > 0` → `INSERT OR IGNORE` then always
  `UPDATE` the `inventory_balances` row to the given quantity (an
  absolute set, not an additive delta) for the auto-selected/first
  branch.
- **Transaction**: one `conn`, one `cur`, all statements in the loop,
  single `conn.commit()` at the very end — **no explicit `try/except`
  rollback** around the loop. Python's `sqlite3` module holds an
  implicit transaction open from the first DML statement until
  `commit()`/`rollback()`/connection close, so a mid-loop exception with
  no explicit handling leaves the transaction uncommitted and the
  connection is simply abandoned (garbage-collected) — accidentally
  safe for a SINGLE handler's own rows, but **not** safe across
  multiple handlers in one `smart_execute` call (see below).
- **Result**: `{'imported': N, 'updated': N, 'skipped': N, 'message': str}`.

### `_handle_retail_customers` (line 1100)

- **Accepted fields**: `name*, phone, email, address, loyalty_points,
  total_spent`.
- **Duplicate key**: `email` (lower-cased) — but **only when non-empty**;
  a row with no email is unconditionally `INSERT`ed as a new customer
  every time, even on repeated import of the same file. Real, disclosed
  gap (no fallback dedup key like name+phone).
- No dependency on any other entity.

### `_handle_retail_suppliers` (line 1129)

- **Accepted fields**: `name*, phone, email, address`.
- **Duplicate key**: `name`, **case-sensitive** exact match. Existing
  match → **silently skipped** (not updated, unlike products/customers).
- No dependency on any other entity.

### `_handle_retail_branches` (line 1147)

- **Accepted fields**: `name*, address, phone, status`.
- **Duplicate key**: `name`, **case-sensitive** exact match → skipped
  (not updated).
- `status` defaults to `'active'` if blank, lower-cased.
- No dependency on any other entity.

### `_handle_retail_categories` (line 1164)

- **Accepted fields**: `name*, description`.
- **Duplicate key**: `name`, **case-sensitive** exact match → skipped
  (not updated).
- No dependency on any other entity.

## Real, disclosed cross-handler inconsistency

Duplicate-match case-sensitivity is **not uniform**: `products` matches
by exact `sku` (case-sensitive by construction, SKUs are typically
case-significant codes anyway), `customers` matches by **lower-cased**
email, but `suppliers`/`branches`/`categories` match by **case-sensitive**
`name` — `"ABC Supply"` and `"abc supply"` would be treated as two
different suppliers. This is real, existing legacy behavior, not
invented — M5.8's own duplicate-policy decision
(`import-duplicate-conflict-policy.md`) must decide explicitly whether
to preserve this exact inconsistency or normalize it, citing this real
finding either way.

## The real pipeline (routes, in call order)

1. `GET /schemas` — returns the static `SCHEMAS` dict (labels/fields per
   entity), used by the frontend wizard to render the mapping UI.
2. `POST /parse` — upload → `_parse_file` → column headers + up to 3
   sample values per column + auto-suggested mapping (`_suggest_mapping`)
   + entity-fit scoring (`_entity_fit`) for the CHOSEN entity + up to 3
   alternative entity suggestions if a materially better fit exists.
3. `POST /detect` — upload → scans the file against **every** Retail
   entity (not just one), returns entities whose required-field coverage
   and "real match" heuristic (`name_specific` — a column literally named
   like the entity, e.g. `productname`/`suppliername` — or a "strong key"
   column like `sku`/`barcode`/`email` present) both pass, sorted by fit
   then dependency order. Powers the "smart"/combined-file import UX.
4. `POST /smart-execute` — upload + `targets` (list of
   `{system, entity, mapping}`) → re-parses the file **once**, sorts
   targets by `_order_index` (categories/suppliers/branches before
   customers/products), runs `_clean_records` + the entity's handler for
   **each target independently** (separate `_clean_records` call per
   target, separate handler call per target — **separate DB connection
   per handler call**, confirmed by each `_handle_retail_*` function
   opening its own `get_retail_conn()`).
5. `POST /clean` — upload + mapping → `_clean_records` only, returns the
   audit report (`log`, `issues`, `categories`, `counts`) and up to 8
   sample cleaned records, **without importing anything**. This is the
   closest existing analogue to a "preview," but it is **not** an
   immutable, hashed, tokenized dry-run — a second `/execute` call
   re-parses the file from scratch with no verification the file matches
   what was previewed.
6. `POST /execute` — upload + mapping (single entity) → re-parses the
   file, re-runs `_clean_records`, calls the one handler, merges the
   cleaning report into the result, computes `status`
   (`ok`/`partial`/`none`) and `warnings`.

## Real, confirmed gaps this milestone must close (not silently repeat)

1. **No immutable dry-run.** `/clean` and `/execute` are two independent,
   unrelated calls — nothing ties a previewed result to the file/mapping
   actually committed. A file could change between the two calls with no
   detection. (M5.8.13/M5.8.14's own explicit requirement.)
2. **No cross-handler transaction.** `smart_execute`'s multi-entity
   import opens one fresh connection per handler/entity — if the
   `products` handler throws after `categories`/`suppliers`/`branches`
   already committed, those earlier entities' rows remain committed.
   Real, confirmed partial-commit risk. (M5.8.15's own explicit
   requirement.)
3. **No idempotency.** Re-submitting the same `/execute` call (e.g. a
   retried HTTP request after a timeout) re-parses and re-imports —
   `products`/`customers` handlers happen to be safe on retry because
   their own dedup keys make a second run an `UPDATE`, but
   `suppliers`/`branches`/`categories` are also safe (name-match skips),
   so **accidental** idempotency exists per-handler today, but nothing
   guarantees it, and there is no explicit idempotency KEY/token
   anywhere. (M5.8.17's own explicit requirement.)
4. **No provenance/audit record.** Confirmed: no table in
   `database/schema.py` stores who imported what, when, from what file,
   with what result. (M5.8.18's own explicit requirement.)
5. **No file-size/resource limits.** `_parse_file`'s XLSX branch calls
   `openpyxl.load_workbook` on the raw upload with no size check first;
   JSON reads the whole body into memory (`file_obj.read()`); SQLite caps
   at 50,000 rows for the *chosen* table only, after already writing the
   entire uploaded file to a temp file and opening it with `sqlite3.connect`
   (no page-count/size limit before that). No ZIP-bomb protection, no
   JSON-depth limit, no CSV field-length limit. (M5.8.3's own explicit
   requirement — this is the most security-sensitive real gap.)
6. **No macro/external-link/formula rejection for XLSX.** `data_only=True`
   reads cached formula results (not live formulas), which incidentally
   avoids formula RE-EXECUTION on read, but there is no explicit check
   rejecting a workbook that contains macros, external links, or
   formulas at all — a workbook could carry them undetected.
7. **SQLite import trusts the uploaded file structurally.** Opens it with
   a plain read-write `sqlite3.connect` (not read-only, no
   `PRAGMA query_only=ON`), runs `SELECT COUNT(*)` per table (which could
   theoretically invoke a malicious `INSTEAD OF` trigger on a view, though
   plain tables are what's targeted here) and picks "the biggest table"
   heuristically rather than an allowlisted, explicitly-required table
   name. No `PRAGMA integrity_check`.
8. **Formula-injection strings are not marked.** A CSV/XLSX cell value
   starting with `=`, `+`, `-`, or `@` is imported as plain text (Python
   `str`), which is actually safe for THIS import path (no formula
   execution happens on import), but nothing marks such values for a
   FUTURE export path to neutralize.

## What is real and already correct, to preserve

- Bilingual (English + Arabic) header alias matching, value-type
  sniffing (email/date/number), and confidence-scored auto-mapping
  (`_suggest_mapping`) — genuinely useful, real, tested behavior
  (`test_entity_auto_detection_within_retail_schemas`) to carry forward
  as real behavioral evidence, not necessarily reused code (Kotlin
  cannot import this Python module).
- Within-file duplicate detection via `_dedup_keys` (`sku`/`barcode`/
  `code`/`email`, falling back to whole-row identity) —
  `test_within_file_duplicate_rows_deduped_not_double_imported` proves
  this real, tested behavior.
- Garbage/malformed input is rejected with a real 400, never a 500
  (`test_garbage_bytes_rejected_not_500`,
  `test_malformed_json_import_rejected_safely`) — real, tested defensive
  behavior to match or exceed, not weaken.
- Missing required fields are skipped, not crashed
  (`test_rows_missing_required_field_are_skipped_not_crashed`).
- Authentication is required on every route
  (`test_parse_requires_authentication`,
  `test_execute_requires_authentication`,
  `test_schemas_requires_authentication`) — real, existing
  `@mt_login_required` gate; the mobile client's own future canonical
  authorization (M5.8.19) is the real analogue, still deferred.
- Re-importing the same SKU updates rather than duplicates
  (`test_reimporting_same_sku_updates_not_duplicates`) — real, tested,
  behavioral evidence for the products handler's dedup policy.

## Android callers

Grepped the entire `mobile/aura-retail-unified/` tree: **no Kotlin code
references import at all yet**, beyond the empty M5.1 boundary marker
`interface ImportRepository` (`data/RepositoryBoundaries.kt`, "M5.8-M5.11
-- shared Import Center, ported architecturally (not line-by-line) from
the real 1186-line Python import pipeline"). This confirms M5.8 is
starting from zero Kotlin import code, consistent with the checkpoint's
own framing.

## Frontend wizard

`products/retail/frontend/import-wizard.js` exists and drives the
`/parse` → `/detect` → `/clean` → `/execute`/`/smart-execute` call
sequence from the browser UI — real evidence of the intended user flow
(upload → preview mapping → preview cleaning → confirm → commit), used
here only to confirm the real route call order above, not inspected
line-by-line (no Kotlin UI work begins this milestone, per the
checkpoint's own "do not begin full Compose Import screens" instruction).
