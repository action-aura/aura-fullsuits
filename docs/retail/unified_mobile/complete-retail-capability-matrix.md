# Aura Retail Unified Mobile — Complete Retail Capability Matrix

**Supersedes** the "6 items pending product-owner decision" classification in `android-feature-parity-matrix.md`'s status summary, per the Product Owner Scope Override: Aura Retail Unified Mobile is a complete Retail mobile product, not a strict translation of the current 17-route Android navigation graph. `android-feature-parity-matrix.md` itself is preserved unmodified as the honest historical record of Milestone 1's first pass; this document is the authoritative, expanded classification going forward.

**Statuses used** (per the override): `FULL_SHARED_MOBILE_FEATURE`, `SHARED_DOMAIN_INTERNAL`, `ADMIN_MOBILE_FEATURE`, `REPORTING_MOBILE_FEATURE`, `PLATFORM_ADAPTER_FEATURE`, `BACKEND_INTERNAL_ONLY`, `DEPRECATED_AND_REMOVED`, `NOT_APPLICABLE` (reason + Product Owner approval required).

## Already-classified capabilities (carried forward from Milestone 1, status re-mapped to the new taxonomy)

The 21 AUDITED + 8 SHARED_IMPLEMENTATION_REQUIRED + 2 hardware-adapter features from `android-feature-parity-matrix.md` all map to `FULL_SHARED_MOBILE_FEATURE` (business features) or `PLATFORM_ADAPTER_FEATURE` (barcode camera, HID scanner) under the new taxonomy — no change in scope, only in label. Not re-listed here in full; see that document for the per-feature detail (screen, route, table, hardware). The `AiSheet` stub remains `NOT_APPLICABLE` (explicit non-functional placeholder, nothing to port). The dead Clinic API surface in `AuraApi.kt` is reclassified below.

## Newly audited: the six previously-flagged capabilities

### 1. Categories — `FULL_SHARED_MOBILE_FEATURE`

**Backend authority**: `retail_api.py` `list_categories`/`create_category` (lines 191-219). **Table**: `categories` (`id, company_id, name, description, created_at` — real schema, `database/schema.py:91-96`).

**Real finding, must be built, not assumed**: the backend has **no `status` column on `categories`**, **no edit route**, **no archive route**, and **no duplicate-name check** (products enforce SKU uniqueness with a 409; categories do not have an equivalent check anywhere in the current backend). This is a genuine backend-capability gap, not a mobile-client gap — "editing", "archive/activate behavior", and "duplicate prevention" listed in the override do not exist in any authority today.

**Implementation approach**: since this initiative already eliminates the embedded Python/Chaquopy server as the mobile runtime (per the core migration mandate), these missing capabilities are implemented as **new shared Kotlin domain logic in `commonMain`** — using the existing `products` table's status-flag-based soft-delete/duplicate-SKU pattern as the canonical behavioral reference (same reasoning class Phase 9.5D/9.5E used repeatedly: extend an existing, proven pattern rather than invent a new one). This is not "a second Category authority" in the forbidden sense (a competing, divergent implementation) — it is the *one* authority, now expressed in the shared core instead of Python, exactly as this whole initiative's core mandate requires for every other feature.

Scope for the shared implementation: list, search, create (name + description, duplicate-name-within-company rejected), edit (name/description), archive (soft, add a `status` column via shared-DB migration, mirroring `products.status`), category assignment (products.category_id already supports this), POS category filter (products screen already groups by category — confirm reuse in Milestone 5), usage protection on archive (block archiving a category with active products, mirroring `delete_product`'s sales-history protection pattern), EN/AR/RTL, Android+iOS.

### 2. Branches — `FULL_SHARED_MOBILE_FEATURE`

**Backend authority**: `retail_api.py` `list_branches`/`create_branch` (lines 1134-1159). **Table**: `branches` (`id, company_id, name, address, phone, status, created_at`).

**Real finding**: branches **do** have a `status` column (unlike categories) and every inventory/sale/purchase-order table already carries a `branch_id` foreign key (`inventory_balances.branch_id`, `inventory_movements.branch_id`, confirmed in `create_product`'s `_default_branch(conn, cid)` call). The backend already resolves a "default/current" branch per company for every stock-mutating operation. **No edit route, no archive route, no branch-switching/selection concept exists in any current caller** (Android has zero branch UI).

**Local-model constraint (explicit, not assumed away)**: this initiative's offline-first, single-device-database architecture means each device holds one local SQLite database. Per the override's own instruction ("do not invent multi-branch cloud synchronization... document and implement the exact supported local model"), the supported model is: **a device operates against exactly one local database, which may itself represent one branch's data or a company's full multi-branch data depending on how that specific installation was provisioned** — identical to how the existing single-branch assumption already works today (`_default_branch` self-heals a branch on fresh installs). Branch *selection* within a single local database (multiple branches' data coexisting in one device's SQLite file, with a "currently operating as branch X" UI concept) is buildable now, purely locally, with zero remote sync. Branch *data living on a different device's separate database* is explicitly not live-synchronized by this initiative (matches the governing spec's own "no full cloud data sync / no multi-branch live inventory synchronization" out-of-scope line).

Scope for the shared implementation: list, create, edit, archive (mirroring the categories archive-protection pattern), branch selector (persisted current-branch preference), branch-aware POS/inventory/sales-history/dashboard/reports (filter by `branch_id`, already present on every relevant table), branch permission enforcement (reuse the existing `mt_require_subsystem`/session-role pattern as the authority to port), EN/AR/RTL, Android+iOS.

### 3. Sales Trend — `REPORTING_MOBILE_FEATURE`

**Backend authority**: `retail_api.py` `report_sales_trend` (lines 1023-1043). Real formula: per-day `SUM(total)` as revenue, `COUNT(*)` as transactions, `AVG(total)` as avg ticket, over the trailing `days` window (default 14). **Not returns-adjusted** — the query reads only `sales`, never nets against `returns`/`return_items`. This is the exact, current canonical definition; the shared `usecases/SalesTrendUseCase` must reproduce this formula exactly (including the non-returns-adjusted behavior) rather than inventing a "more correct" netted version, since that would silently diverge from the canonical service's own defined output — if a returns-adjusted trend is wanted, that is a new canonical-authority decision, not a mobile-client one.

Scope: daily/weekly/monthly period buckets (weekly/monthly require new shared aggregation over the same `sales` rows — the backend route only supports a rolling N-day window today, no explicit weekly/monthly bucket parameter; build this as shared Kotlin aggregation logic, same reasoning as Categories/Branches above), responsive chart rendering, empty/loading states, EN/AR/RTL, Android+iOS.

### 4. Top Products — `REPORTING_MOBILE_FEATURE`

**Backend authority**: `retail_api.py` `report_top_products` (lines 1046-1069). Real, exact metric definitions (must be preserved verbatim, not paraphrased): `units_sold = SUM(sale_items.quantity)`; `revenue = SUM(sale_items.line_total)`; `cost = SUM(sale_items.quantity * products.cost_price)`; `profit = revenue - cost`; **ranking basis is `units_sold DESC`** (quantity-based ranking is the default and only current ranking — there is no revenue-based ranking toggle in the current query, contrary to the override's "revenue-based ranking when canonically supported" phrasing; revenue-based ranking does not currently exist as a canonical option and would be new shared-core logic, not a port).

Scope: quantity-ranked list (canonical, port as-is), revenue-ranked view (new shared sort option, same data), date-range parameter (route currently has no date filter at all — only an implicit all-time aggregate with a `limit`; date-range is new shared-core scope), branch/category filters (new, using the same `branch_id`/`category_id` columns already on `products`/`sale_items`' joined tables), drill-down to product detail or sale history (both already exist as separate screens/routes — wire navigation, not new data logic), currency separation, EN/AR/RTL, Android+iOS.

### 5. Import API — `ADMIN_MOBILE_FEATURE` (with `SHARED_DOMAIN_INTERNAL` for the parsing/validation engine)

**Backend authority**: `products/retail/backend/api/import_api.py`, 1186 lines, 6 routes (`/schemas`, `/parse`, `/detect`, `/smart-execute`, `/clean`, `/execute`), 5 Retail entity handlers (`_handle_retail_{products,customers,suppliers,branches,categories}`).

**Real, confirmed supported formats** (from `_parse_file`, lines 165-230): **CSV** (generic delimiter-sniffed text), **XLSX/XLS** (via `openpyxl`, `data_only=True` — reads cached cell values, does not evaluate formulas on read), **JSON** (array-of-objects), **SQLite** (`.db`/`.sqlite`/`.sqlite3` — parses the largest table by row count as a heuristic, capped at 50,000 rows).

**Real, confirmed pipeline** (matches the override's required flow almost exactly, already built): `/schemas` (entity field definitions) -> `/parse` (raw file -> headers+rows) -> `/detect` (fuzzy header-matching incl. Arabic aliases, entity auto-suggestion across the 5 Retail entities) -> `/smart-execute` or `/clean` (preview/dry-run: validates, dedupes, reports issues **without committing**) -> `/execute` (re-parses the file + confirmed mapping, commits via the entity handler). This is already a real dry-run-then-commit design, not something to invent from scratch — port it as-is into the shared core.

**Real gaps found in the current backend that must be closed, not silently ported** (this is exactly the kind of finding this whole engagement's discipline requires surfacing, not assuming away):

- No visible file-size limit inside `_parse_file` itself (may rely on Flask's `MAX_CONTENT_LENGTH`, unverified in this pass — must be confirmed in Milestone 5, and a real bounded limit enforced in the shared core regardless).
- No magic-byte validation — files are trusted by extension/filename only.
- No explicit path-traversal or malicious-filename handling visible in `_parse_file` (uploads are read as in-memory streams, not written to attacker-controlled paths, which structurally avoids traversal — but this must be explicitly verified, not assumed, when the shared Kotlin core takes over file I/O on-device where a real filesystem path is involved).
- `.xlsx` formula-injection risk: `data_only=True` reads cached values only, which is the correct defense against formula *execution*, but does not prevent a malicious formula string being read as a literal cell value if `data_only=False` were ever used — must be preserved exactly as `data_only=True` in the shared port, and tested.
- No transactional rollback visible across `execute_import`'s full batch at the route level (delegated to each `_handle_retail_*` handler; whether each handler wraps its own inserts in a single SQLite transaction was not yet verified line-by-line in this pass — required before Milestone 5 closes this feature, not before this matrix's own entry).

**Import Center mobile scope**: file selection (Android `FilePicker`/iOS `UIDocumentPicker` adapters), preview with detected type/columns/entity + Arabic-alias-aware suggested mapping, mapping review, validation summary (reuse the exact `_clean_records` issue-reporting shape), dry-run (`/detect`+`/clean` equivalent, ported to shared Kotlin, no commit), confirm-and-import (ported `/execute` equivalent, single local-DB transaction per entity batch — closing the transactional-rollback gap explicitly rather than inheriting the current ambiguity), progress, success/failure report with per-row error detail (already shaped as `row_errors`/`warnings` in the real response), EN/AR (the real alias vocabulary already includes Arabic column-name matching — `ARABIC_ALIASES`, confirmed present), safe retry, audit trail (reuse the existing `_audit()` pattern already used by `create_product`/etc.), Android+iOS. The parsing/detection/validation engine itself is `SHARED_DOMAIN_INTERNAL` (never duplicated between Android and iOS UI code, per the override's explicit instruction); the screens are `ADMIN_MOBILE_FEATURE`.

### 6. Dead Clinic API surface in `AuraApi.kt` — `DEPRECATED_AND_REMOVED`

Confirmed (Milestone 1): `net/AuraApi.kt` lines 26-73 define 11 Clinic-domain Retrofit methods (patients, appointments, prescriptions, invoices, doctors, services, lab-expenses) with **zero call sites** in `ui/AppRoot.kt`'s real navigation graph. Per the override: remains out of scope, will be deleted (not migrated) during Milestone 5's shared-repository build, proven by a zero-reference grep before deletion. No Clinic models, requests, or screens carry into the shared core. The real Clinic product (`products/clinic/`) is not touched by this initiative.

## Remaining backend routes already covered by Milestone 1 (no reclassification needed)

All other 42 of the 48 `retail_api.py` routes (dashboard, products CRUD + stock-adjust, sales, customers + AR, suppliers + AP, purchase orders, payments, daily-cash, aging, settings, payment-methods, returns, reports/summary, reports/payment-methods) were already mapped to real screens/features in `android-feature-parity-matrix.md` and retain `FULL_SHARED_MOBILE_FEATURE` status, several already flagged `SHARED_IMPLEMENTATION_REQUIRED` there for the financial core (unchanged meaning under the new taxonomy).

## Updated navigation target (additive to Milestone 6's eventual scope)

Beyond the 17 routes already in `AppRoot.kt`, the complete product requires these additional real screens, each backed by the real service/table above, not placeholder data:

`category_list`, `category_edit`, `branch_list`, `branch_edit`, `branch_selector`, `sales_trend`, `top_products`, `import_center` (with sub-steps: file-select, preview/mapping, validation-summary, confirm).

## Status summary (this document's scope only)

| Status | Items |
|---|---|
| `FULL_SHARED_MOBILE_FEATURE` | Categories, Branches (both requiring new shared-core domain logic beyond the current backend's capability, per the local-model/no-cloud-sync constraint documented above) |
| `REPORTING_MOBILE_FEATURE` | Sales Trend, Top Products (both requiring new shared-core aggregation beyond the current backend's single-window/single-ranking limitation) |
| `ADMIN_MOBILE_FEATURE` + `SHARED_DOMAIN_INTERNAL` | Import Center (screens + shared parsing/validation engine) |
| `DEPRECATED_AND_REMOVED` | Dead Clinic API surface |

Zero items in this document are marked `NOT_APPLICABLE` — every previously-flagged capability received a real classification and a real implementation path, per the override's instruction that "no current Android caller" is no longer sufficient grounds for that status.

## Open verification items (honest, carried into Milestone 5, not blocking Milestone 2)

- Whether `_handle_retail_products`/`_handle_retail_customers`/etc. each wrap their batch inserts in one SQLite transaction (import rollback behavior) — not yet read line-by-line.
- Whether Flask's `MAX_CONTENT_LENGTH` bounds import file size today.
- `RetailSettingsScreen`'s exact tax-settings call-site (carried over from Milestone 1, still open).
