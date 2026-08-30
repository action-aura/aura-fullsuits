# Product variants + restaurant modifiers — design

**Status:** DESIGN ONLY. No source file changed. Written 2026-08-31 against
`feat/launch-readiness` (worktree `ci-hardening-w0.3-continue`), retail schema
**v23 on disk** (promotions wave 1 landed), **v24 RESERVED BY NAME** (not
claimed) for inter-branch transfers per ROADMAP.md's 2026-08-30 entry.

**Question index** (the brief's "must settle" list): (1) schema §3.2 / §4.2 ·
(2) create_sale + pricing.py §3.3 / §4.3 · (3) returns §3.4 / §4.4 ·
(4) scan path §3.5 · (5) sync §3.6 / §4.8 · (6) POS screen §3.7 / §4.9 ·
(7) invisible-unless-opted-in §3.9 / §4.11 · (8) reporting §3.8 / §4.10 ·
modifier groups/min-max §4.5 · modifier cost §4.6 · promotions × modifiers
§4.7 · neighbours §5 · sequencing §6 · risks §7 · owner questions §8.

---

## 0. Verification of the premise — and three findings that reshape the design

Both absences re-verified by reading, not trusted from the brief:

* **No variants.** `products` is a flat row: no `parent_product_id`, no option
  columns, in either the base DDL (`products/retail/backend/database/schema.py:5354`)
  or any migration through v23. The only "variant" strings in the backend are
  import-header aliases (`api/import_api.py:373,379` — `variantsku`/
  `variantbarcode` map a foreign CSV's column names onto the flat `sku`/`barcode`)
  and prose in unrelated comments.
* **No modifiers.** Zero schema, zero API, zero frontend. Every `modifier` hit
  in the backend is SQLite datetime-modifier prose in `core/retail/metrics.py`.

Three things found while verifying that materially shape what follows:

**Finding A — products are a synced entity; the base-DDL comment says otherwise
and is stale.** `RETAIL_SYNC_ENTITY_TYPES`
(`commercial_runtime/sync/sync_service.py:302`, module docstring lines 8–16)
includes `product` as one of the five mutable catalogue types, upserted
`ON CONFLICT(id) DO UPDATE` with a `row_version` reject-stale gate
(`sync_service.py:1369` product branch). `products.id` is a client-generated
TEXT UUID since v4 (`schema.py::_migrate_products_to_uuid`) and **is itself the
wire identity** — unlike `branches.id`, which needed a separate `branch_uid` on
every payload (AUDIT-032C). The comment inside the base `products` DDL
("products are not synced", `schema.py:~5370`) predates the catalogue-sync
expansion and is wrong today. Consequence: **a variant modelled as a product row
whose parent pointer is another product's UUID syncs across devices with zero
new sync machinery** — the pointer means the same thing on every device. No
other variant model gets that property for free.

**Finding B — "one product = one sale line" is guaranteed by the POS client,
not by the API, and modifiers break exactly that guarantee.**
`create_return` resolves the original line by `(sale_id, product_id)` with a
bare `fetchone()` (`api/retail_api.py:5245`), and its remaining-returnable
arithmetic aggregates per `product_id`. That is safe today only because
`_addToCart` **merges** same-product adds into one cart line
(`frontend/subsystem-retail.js:2381`), so the POS cannot produce two lines of
one product with different prices. Variants preserve the guarantee (each
variant is its own `product_id`); modifiers destroy it (plain burger + extra-
cheese burger = two lines, same `product_id`, different unit prices). This
single fact decides the returns question in §3.4/§4.4: **variants get the free
ride, modifiers do not.**

**Finding C — anything folded into `sale_items.unit_price` is automatically
correct in returns, reports, and sync; anything stored beside it silently
breaks all three.** `create_return` recomputes refunds by re-running
`pricing.calculate_line` on the stored line's `(unit_price, discount_pct,
tax_rate)` (`retail_api.py:5266`). `top_products` deliberately does the same
re-pricing so per-product revenue sums back to `SUM(sales.total)` exactly
(`core/retail/metrics.py:1277–1315` and its "#7" comment). The `sale_item` sync
payload carries `unit_price` verbatim (`retail_api.py:~4210`). So the one
load-bearing decision for modifiers is: **the modifier-inclusive price must BE
the line's `unit_price`**, not a separate additive amount. §4.3 builds on this.

---

## 1. Recommendation up front

1. **A variant is a PRODUCT with a parent** — `products.parent_product_id`
   (TEXT, another product's UUID) plus `products.variant_label`. The brief's
   prior survives testing and comes out stronger than stated: beyond reusing
   the inventory subsystem, it also reuses the entire catalogue-sync machinery
   (Finding A), the v22 scan ladder, tombstones, `row_version` reject-stale,
   import, and every report keyed on `product_id`. The alternative
   (`product_variants` table with its own stock) is costed and rejected in §3.1.
2. **A modifier is genuinely new schema** — three sync-ready config tables plus
   a per-line snapshot table (`sale_item_modifiers`), integrated the way
   promotions were: the server resolves modifier selections to an **effective
   unit price** and feeds it through the existing `pricing.calculate_line`.
   **`pricing.py` survives untouched for both features.**
3. **The two must not share a mechanism** — argued in §2.
4. **Order: variants first.** Modifiers are roughly 2–2.5× the build of
   variants on their own, and a restaurant is not sellable until kitchen
   tickets and split tender/tips also exist (§5), putting the full restaurant
   distance at roughly 4–5× variants. Variants alone complete the apparel/
   hypermarket story, because everything else that vertical needs is already
   built. Full argument and the test of the owner's claim in §6.
5. **Version numbers:** claim **v25 for variants** and **v26 for modifiers** in
   ROADMAP.md *before* any build is dispatched, leaving **v24 as a named gap**
   for inter-branch transfers. The v24 note explicitly says it does not hold
   the number hostage, but taking it silently is exactly the collision the
   ROADMAP ledger exists to prevent — and this chain already has a blessed
   precedent for a deliberate gap (the v10 gap, `schema.py:139–180` comment).
   A database stepping 23→25 is fine: `ensure_schema_version` compares
   integers, it does not require every integer to have been used.

---

## 2. Why variants and modifiers must not share one mechanism

The two look alike on a menu-builder screenshot ("options on an item") and are
opposites everywhere this codebase actually does its work:

| Axis | Variant (Red / L) | Modifier (extra cheese +0.50) |
|---|---|---|
| Stock | **Counted.** Own balance row, own movements, own oversell refusal, own drift repair, own `stock_exceptions` row | **None.** Never appears in `inventory_balances` |
| Identity | Own SKU, own barcode — **scannable** | No barcode. Chosen, never scanned |
| Price | Own `sell_price`, own `cost_price`, own `tax_rate` | A **delta** against the host line's price |
| Sale line | IS the line's `product_id` | An **annotation on** a line, N per line |
| Returns | Returned as itself | Returned only as part of the line it rode on |
| Receipt | A line | Indented sub-text under a line; must reach a kitchen ticket |
| Lifetime | Permanent catalogue fact | Config that changes weekly (seasonal menu) |

A single shared "options" abstraction has to betray one side or the other:
either modifiers get fake stock rows (poisoning drift reconciliation, the
oversell guard, `stock_exceptions`, and Phase 3's ledger-is-truth invariant
with rows that can never reconcile), or variants lose real stock (re-creating
the exact gap this design exists to close). Every POS that survives contact
with both verticals keeps them separate. So: **two designs, two schema
versions, two waves — sharing only the integration *pattern*** (resolve to a
value `pricing.calculate_line` already accepts; snapshot what was applied).

---

## 3. Variants — a product with a parent

### 3.1 The model, tested against its alternative

The prior: `products.parent_product_id` + a variant label. Tested by costing
the alternative — a `product_variants` table carrying its own stock — against
what would have to be duplicated or forked:

* `inventory_balances`/`inventory_movements` are keyed by `product_id`
  (`schema.py:5376,5390`). A second stock-bearing key means either a parallel
  pair of tables or a polymorphic key in the existing pair — both fork the
  oversell check in `create_sale` (`retail_api.py:3935–4005`), the balance
  write loop, `stock_reconciliation.compute_drift/repair_drift`, the
  `stock_exceptions` writers (v20), and the `inventory_movement` sync-apply
  branch that atomically re-derives balances on peer devices.
* `sale_items.product_id`, `return_items.product_id`,
  `purchase_order_items.product_id` all FK to `products(id)` — a variant that
  is not a product cannot be sold, returned, or purchased without a
  polymorphic reference in three money tables.
* The v21/v22 scan ladder indexes `products` columns; a separate table means a
  fifth and sixth rung or a UNION.
* `product` is a synced entity (Finding A); a new table means a new entity
  type, emission sites, an apply branch, and a reject-stale story — all of
  which the products row already has.

The alternative duplicates the most safety-audited subsystem in the codebase
to gain nothing the parent-pointer model lacks. **Prior confirmed.** One
refinement the prior did not state: the parent row becomes a *grouping*
product that must itself be refused at the till (§3.3), because selling the
abstract "Polo Shirt" would corrupt per-variant stock attribution.

Wave 1 deliberately models the variant *label* as free text ("Red / L"), not a
normalized option matrix (`option_types`/`option_values`/`product_option_
values`). The matrix buys filterable attributes and a size-×-color generator
UI at the cost of three more tables and their sync; nothing in the sale, scan,
stock, or return path needs it. It can be added later *under* the same
parent-pointer model without unwinding anything. (Owner question in §8.)

### 3.2 Schema — v25, claimed in ROADMAP.md first

Two columns and one index. Pure additive `ALTER TABLE ADD COLUMN` — no
`_new`-table rebuild, same shape as v5/v6/v8.

```sql
ALTER TABLE products ADD COLUMN parent_product_id TEXT;  -- another products.id (UUID)
ALTER TABLE products ADD COLUMN variant_label TEXT;      -- "Red / L"; NULL on non-variants
CREATE INDEX IF NOT EXISTS idx_products_parent
    ON products(company_id, parent_product_id)
    WHERE parent_product_id IS NOT NULL;                 -- partial: costs nothing to non-adopters
```

**Deliberately NO declared FOREIGN KEY** on `parent_product_id`, for the same
two reasons `promotions.product_id` has none (`_migrate_add_promotions`
docstring): (a) never block a sale or a sync apply on a referential
technicality; (b) sync arrival order — a variant's `product` event can apply
before its parent's on a peer device (delta updates and quarantine can reorder
past outbox order), and a declared FK would abort the whole pull batch.
Ownership is validated at API write time instead: create/update refuse a
`parent_product_id` that does not resolve to a same-company, non-tombstoned,
**non-variant** product (no grandparents — a parent that is itself a variant is
refused; one level, matching the flat label model).

Base DDL (`_init_retail`) gains the same two columns and the index so a fresh
install gets v25 shape directly — same convention as `supplier_contacts`/
`min_order_value` ("included here too so a brand-new install… without ever
running that migration").

### 3.3 create_sale, and whether pricing.py survives

**pricing.py is untouched — trivially.** A variant is a product row with its
own `sell_price`/`tax_rate`; `create_sale` already resolves both from the
product row (`retail_api.py:3919` SELECT) and feeds `calculate_line`. A
variant sale IS a product sale. Nothing to integrate.

Two additions inside the existing line loop:

1. **The parent guard.** The line-resolution SELECT gains `parent_product_id`
   plus an `EXISTS(SELECT 1 FROM products c WHERE c.parent_product_id = p.id
   AND c.company_id = p.company_id AND c.deleted_at_utc IS NULL AND
   c.status='active') AS has_variants`; a line whose product `has_variants`
   is refused 400 `"'Polo Shirt' has variants — choose one."` before any row
   is written (same total-refusal posture as every other line check).
   Deliberately **not** a new `status` value — Phase 6 already paid for
   overloading `status` once (the tombstone saga); the guard derives from live
   data instead of a second flag that can drift.
   **The allow-half must be proven, not assumed** (ENGINEERING §1): a product
   with zero children, and a *variant* row itself, must both still sell —
   mutation-prove by breaking the EXISTS to `1=1` (every sale refused → test
   red) and to `0=1` (parent sellable → test red).
2. **Promotions gain a parent tier.** `resolve_line_discount_pct`
   (`core/retail/promotions.py:81`) matches `promo.product_id == line
   product_id` — a promotion configured on the parent ("all Polo Shirts 20%
   off") would silently miss every variant. Fix in the resolver, not in
   pricing: `create_sale` passes the line's `parent_product_id` through, and
   the tier order becomes **variant-specific > parent-level > category**,
   preserving wave 1's "specific beats general regardless of size" rule and
   `max()` best-price semantics unchanged. ~15 lines + tests.

### 3.4 Returns — the free ride, traced

`create_return` matches the original line by `(sale_id, product_id)`
(`retail_api.py:5245`) and recomputes the refund from that line's stored
`unit_price/discount_pct/tax_rate`. A variant is its own `product_id`, the POS
merges per `product_id` (Finding B), so the match is unambiguous, the refund
figures are the variant's own, and `return_in` stock lands on the variant's
own balance row. **Zero change to `create_return`.** Same free ride promotions
got, for the same structural reason: the thing that changed the price lives
*inside* the columns returns already read.

### 3.5 The scan path

The v22 four-rung ladder (`lookup_product`, `retail_api.py:1567`) works
untouched: a variant's barcode/SKU are ordinary `products.barcode`/`sku`
values covered by the existing exact + NOCASE indexes, and `create_product`'s
company-scoped SKU/barcode uniqueness guards apply per variant automatically.
Scanning a **variant** barcode resolves straight to the sellable row — the
common case takes rung 1 and never sees a picker. Scanning the **parent's**
barcode (parents may keep one) resolves to the parent; the response gains
`has_variants` (and `parent_product_id`/`variant_label` ride `p.*` for free),
and the POS opens the picker instead of adding. One additive response field;
no rung changes.

### 3.6 Sync — free, with one trap that must be named

Free because of Finding A: the variant row rides the existing `product` entity,
and its parent pointer is a UUID that means the same thing on every device —
no `branch_uid`-style companion field needed.

**The trap:** a new products column does NOT sync by existing. Three sites must
each gain the two columns, and forgetting any one of them makes variants
arrive on peer devices as orphan standalone products (scan and stock still
work; grouping and the parent guard silently don't):

1. the `product` create payload (`retail_api.py:1802`) and update payload/
   `_changed_fields` (`:1934`);
2. the apply-branch INSERT column list and the `_delta_set_clause` allowlist
   (`sync_service.py:1369–1440`) — `p.get("parent_product_id")` with NULL
   default, exactly the `reorder_method` precedent that branch's comment
   documents;
3. `import_api.py`'s column-alias map, so `variantsku`-style CSVs can
   round-trip a parent link (wave 1: import `parent_sku` by lookup).

The verifier's checklist for this wave should include a two-device test:
create parent + variant on device A, pull on device B, assert B's row carries
the pointer — not just "a product arrived."

### 3.7 The POS screen

* **Grid:** variant rows are excluded from the tile grid (`parent_product_id
  IS NULL` filter client-side); the parent tile shows a small "N options"
  badge. Tapping it opens a **variant picker** — a flat grid of the children
  (label, price, per-variant stock), one tap to add. Two taps total for a
  browse-add; **one tap/scan unchanged** for the barcode path, which is the
  path apparel tills actually live on.
* **Cart:** the line shows `name — variant_label`; quantity merge continues to
  key on `product_id`, which now IS the variant. No cart data-model change.
* **Data:** the picker needs the children of one parent. `list_products`
  already returns all rows; client groups by `parent_product_id`. At v21-scale
  catalogues, add `GET /products/<id>/variants` (a single indexed SELECT on
  `idx_products_parent`) so the scan-first flow never needs the full array.

### 3.8 Reporting

* **Stock-by-variant falls out** — each variant has its own balance/movement
  rows; the existing stock views are already per-product.
* **Sales-by-variant falls out** — `top_products` is per `product_id`.
* **Parent roll-up needs (small) work:** "Polo Shirt total across colours" is
  `GROUP BY COALESCE(parent_product_id, product_id)` — one optional parameter
  on `top_products` / the stock report, wave 1-adjacent, not blocking.

### 3.9 Invisible unless opted in

An install that never sets a parent pointer: both columns NULL everywhere, the
partial index holds zero rows, the parent guard's EXISTS probes an empty index
(same per-sale cost class as v23's `load_active_promotions` on a
no-promotions install — the established precedent for "invisible"), the picker
never renders, sync payloads carry two NULLs. Byte-identical behaviour; the
only cost is nanoseconds against an empty index.

### 3.10 Explicitly out of wave 1

Option matrix + variant-generator UI (§3.1), per-variant images, parent-level
price inheritance (each variant owns its price outright — inheritance invites
"which price won?" audits), grandparent chains, variant-aware PO generation UX
(POs already work per variant since a variant is a product).

---

## 4. Modifiers — new tables, snapshots, and an effective unit price

### 4.1 The model

Three **config** tables (catalogue-like, mutable, admin-owned) plus one
**snapshot** table (immutable, money-adjacent, written only by `create_sale`).
Config ids are client-generated **TEXT UUIDs** with `row_version`/
`updated_at_utc`/`deleted_at_utc` from day one — the `cash_sessions`/
`reorder_requests` convention — so they *can* ride sync later without the
retrofit `promotions` (INTEGER autoincrement ids) has permanently locked
itself out of. That is a deliberate divergence from the v23 precedent, and it
is cheap insurance, not speculative generality: restaurants are multi-till by
default (§4.8).

### 4.2 Schema — v26, claimed in ROADMAP.md first

```sql
CREATE TABLE IF NOT EXISTS modifier_groups (
    id TEXT PRIMARY KEY,                 -- client-generated UUID
    company_id INTEGER NOT NULL,
    name TEXT NOT NULL,                  -- "Size", "Extras", "Cook level"
    min_select INTEGER NOT NULL DEFAULT 0,   -- >=1 means required
    max_select INTEGER,                  -- NULL = unlimited
    status TEXT DEFAULT 'active',
    row_version INTEGER NOT NULL DEFAULT 1,
    updated_at_utc TEXT, deleted_at_utc TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS modifier_options (
    id TEXT PRIMARY KEY,
    company_id INTEGER NOT NULL,
    group_id TEXT NOT NULL,              -- modifier_groups.id; NO literal FK (v23 precedent)
    name TEXT NOT NULL,                  -- "Extra cheese", "No onion"
    price_delta REAL NOT NULL DEFAULT 0, -- may be negative; see §4.3 clamp
    cost_delta  REAL NOT NULL DEFAULT 0, -- margin reporting only; see §4.6
    is_default INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    status TEXT DEFAULT 'active',
    row_version INTEGER NOT NULL DEFAULT 1,
    updated_at_utc TEXT, deleted_at_utc TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS product_modifier_groups (   -- attach a group to a product
    id TEXT PRIMARY KEY,
    company_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,            -- products.id; attaches to a PARENT apply to no one --
    group_id TEXT NOT NULL,              --   attachment is per concrete product in wave 1
    sort_order INTEGER NOT NULL DEFAULT 0,
    row_version INTEGER NOT NULL DEFAULT 1,
    updated_at_utc TEXT, deleted_at_utc TEXT,
    UNIQUE(company_id, product_id, group_id)
);
CREATE TABLE IF NOT EXISTS sale_item_modifiers (       -- the snapshot; sale_item_promotions' sibling
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    sale_item_id INTEGER NOT NULL,       -- sale_items.id (local autoincrement, like v23)
    modifier_option_id TEXT,             -- provenance only; NEVER re-read for money
    group_name_snapshot TEXT NOT NULL,
    name_snapshot TEXT NOT NULL,
    price_delta_snapshot REAL NOT NULL,  -- AS APPLIED, per unit
    cost_delta_snapshot REAL NOT NULL DEFAULT 0,
    uid TEXT                             -- v13 convention; partial-unique-indexed, sync-ready
);
CREATE INDEX IF NOT EXISTS idx_modifier_options_group ON modifier_options(company_id, group_id);
CREATE INDEX IF NOT EXISTS idx_product_modifier_groups_product
    ON product_modifier_groups(company_id, product_id);
CREATE INDEX IF NOT EXISTS idx_sale_item_modifiers_item ON sale_item_modifiers(sale_item_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sale_item_modifiers_uid
    ON sale_item_modifiers(uid) WHERE uid IS NOT NULL;
```

Plus **one column on an existing money table** (the only one this design
touches, needed by §4.4):

```sql
ALTER TABLE return_items ADD COLUMN sale_item_id INTEGER;  -- nullable; NULL = legacy/aggregate return
```

All additive; CREATE TABLE / ADD COLUMN / CREATE INDEX only.

### 4.3 create_sale, and whether pricing.py survives

**pricing.py survives untouched** — by the same move promotions made, applied
to the other input of `calculate_line`. Promotions resolve to
`discount_pct`; modifiers resolve to the **unit price**:

```
per line:
  selections = payload item's modifier_option_ids            # intent only — never prices
  validate: every option live, attached to this product via its group,
            per-group count within [min_select, max_select],
            every min_select>=1 group attached to this product satisfied
  effective_unit_price = max(0, product.sell_price + Σ price_delta)   # floor at 0
  calc = pricing.calculate_line(effective_unit_price, qty,
                                effective_discount_pct, tax_rate, mode)   # UNCHANGED CALL
  INSERT sale_items (..., unit_price = effective_unit_price, ...)
  INSERT sale_item_modifiers snapshot rows (name/group/price_delta AS APPLIED)
```

The AUDIT-002/003 contract shifts one word and keeps its meaning: unit price
comes from the product row **plus server-resolved modifier deltas** — the
client still submits only intent (option *ids*), never a price, and every
figure is recomputed server-side. Deltas are per unit; quantity multiplies
them automatically because they live inside `unit_price`.

**Why folding into `unit_price` is the load-bearing decision (Finding C):**
storing deltas beside the line instead would make `create_return`'s re-pricing
refund the base price only (money leak on every modified-item return),
`top_products` under-report revenue and stop summing to `SUM(sales.total)`
(violating the exact property `metrics.py:1285–1294` documents fighting for),
and the `sale_item` sync payload under-state the line on every peer device.
Folded in, all three are correct with **zero changes** to returns math,
reports, or sync payloads.

Consequences, stated:

* The receipt's per-unit price is the modifier-inclusive figure, with snapshot
  rows as indented sub-lines (`+ Extra cheese 0.50`, `- No onion`). The
  snapshot is display/audit provenance; `unit_price` is the money.
* Negative deltas are allowed ("small −0.30"); the floor-at-0 clamp mirrors
  `clamp_discount_pct`'s posture — bad config must never produce a negative
  line, and must never block a sale.
* Snapshot rows are written from the validated in-memory selection in the same
  transaction, `sale_item_id` captured via the same `cur.lastrowid`-immediately
  discipline v23's `sale_item_promotions` write documents (`retail_api.py:4213`).
* Validation failure is a 400 **naming the group** ("Choose a size for
  'Burger'"), before `BEGIN IMMEDIATE`, same never-take-the-lock posture as
  the CAP_DISCOUNT and offline-stop refusals. A selection referencing a
  tombstoned/foreign option is refused outright — never silently dropped
  (silently dropping a "no peanuts" modifier is not a pricing bug, it is a
  safety bug).

### 4.4 Returns — NOT a free ride. Traced, and the fix

Promotions rode free because their effect lands in `discount_pct`. Modifiers'
effect lands in `unit_price` — which returns also re-read, so the *arithmetic*
rides free — but modifiers break the **line identity** assumption underneath
it (Finding B): the cart must stop merging same-product lines whose modifier
sets differ (§4.9), so a restaurant sale routinely holds two `sale_items` rows
with the same `product_id` and different unit prices. Against that sale,
`create_return`'s `(sale_id, product_id)` `fetchone()` picks an arbitrary row:
refund the plain burger at the extra-cheese price, or vice versa — a real
money defect, reachable the first week a restaurant processes a return.

(Latent today too: the API never forbade duplicate-product lines, only the POS
never produced them. Modifiers turn a latent edge into the common case.)

**The fix — line-addressable returns, additive and backward-compatible:**

1. Request items accept an optional `sale_item_id`. When present: resolve THAT
   row (validated `sale_id`+`company` scoped), compute remaining-returnable
   **per line** (`return_items.sale_item_id = ?`), refund from that line's own
   figures. When absent: exact current behaviour (aggregate per product) —
   correct whenever the sale has one line per product, i.e. every existing
   sale and every non-modifier install forever.
2. Server guard where it matters: if the target sale has duplicate
   `product_id` lines and the request does not address lines, refuse 400
   ("this sale needs line-level return") rather than guess. Mutation-proof:
   build the duplicate-line sale, return without `sale_item_id`, assert
   refusal; break the guard, assert a wrong-price refund gets caught red.
3. `return_items.sale_item_id` (nullable, §4.2) records the addressed line.
4. Sync: the `return_item` payload gains `sale_item_uid` (nullable) — the
   addressed line's v13 `uid`, resolved on the receiving device via the same
   `_local_id_by_uid` helper `sale_item` parents already use; unresolvable →
   NULL, never quarantined (refund figures are already on the row; the line
   link is attribution, not money).
5. The POS return screen lists the sale's lines (with modifier sub-text from
   the snapshots) instead of a per-product aggregate, and sends
   `sale_item_id` per returned line.

This is the one place modifiers touch a money path beyond `create_sale`. It is
also the piece most tempting to defer — do not: a returns path that guesses
between prices fails silently and only in the vertical this feature exists to
win.

### 4.5 Required vs optional, min/max, and the skipped-required case

* `min_select >= 1` ⇒ required; `max_select` NULL ⇒ unlimited;
  `min=0,max=1` ⇒ optional single-choice; `min=1,max=1` ⇒ forced choice.
  Validated `min <= max` at write time.
* **Till:** the picker's Add button stays disabled until every required group
  meets `min_select`, with the unmet group highlighted — the cashier is
  blocked *in the picker*, not at checkout, because a checkout-time failure
  costs the whole cart's momentum.
* **Server:** independently re-validates (client state is never trusted) and
  returns the 400-naming-the-group of §4.3. Both directions proven: required-
  skipped refused, AND optional-skipped + defaults-only both still sell (the
  allow-half, per ENGINEERING §1).
* **Defaults** (`is_default`) are what keep the till fast — see §4.9.

### 4.6 Modifier cost — decided, not dodged

`modifier_options.cost_delta`, snapshotted per line, **feeds margin reporting
only. No stock movement. Ever.** Reasoning, stated once: extra cheese *is*
stock somewhere — but so are the bun, the patty, and the lettuce in the base
burger, and none of those deplete either, because prepared-food ingredient
depletion is a **recipe/BOM engine** (ingredient products, per-item recipes,
yield/waste), which this product does not have. Depleting ingredients *only
when they arrive via a modifier* would make the cheese ledger precisely wrong:
every "extra cheese" counted, every regular cheeseburger's cheese invisible —
a half-ledger that fails Phase 3's ledger-is-truth bar and poisons drift
repair. So wave 1 draws the honest line: modifiers carry cost for margin
truth; ingredient inventory is a separate, later, opt-in engine (base products
and modifiers together), or stays on the periodic stock-count workflow
restaurants already use. If an owner needs one countable add-on tracked today,
the answer is a **variant** ("Burger" / "Burger + cheese") — countable things
get counted; the mechanisms stay unmixed (§2).

### 4.7 Interaction with promotions (v23) — decided

**A percentage promotion discounts the modifier-inclusive price.** It falls
out of the composition — promotions resolve `discount_pct`, modifiers resolve
`unit_price`, and `calculate_line` multiplies the two — and it is also the
correct answer: "20% off burgers" on a configured burger means 20% off what
the customer is buying; discounting only the base while surcharges ride full-
price is a till argument at the counter. The alternative (exempt modifier
deltas from the discount base) is the one thing in either design that would
force a `pricing.py` change (a second price input to `calculate_line`), and it
buys a worse answer. Rejected. Order of operations, pinned by test: deltas
fold in first; `max(promo, manual)` best-price-wins is judged after and
unchanged; `CAP_DISCOUNT` still gates the **manual** component only, judged
before promotion resolution, exactly as v23 pinned (`schema.py` v23 comment,
"the shop's own weekend offer would lock out its own till").

### 4.8 Sync — deliberately staged, schema-ready from day one

**Wave M1 (this design): config tables and snapshots are NOT synced entity
types** — matching the v23 promotions precedent exactly (`create_sale`'s "No
sync event" comment at the `sale_item_promotions` write). **Money still
converges correctly meanwhile**, because the modifier-inclusive `unit_price`
rides the existing `sale_item` payload (Finding C): every peer device's
totals, Z-figures, and reports are right. What a peer lacks is *detail*:
modifier sub-lines on a cross-device reprint, and the config itself
(groups/options must be entered per till).

Stated loudly rather than hidden: **a multi-till restaurant on wave M1 enters
its menu's modifier config on each till, and cross-device reprints show
line-level prices without modifier text.** That is survivable for a pilot and
wrong for the marketed vertical — which is why, unlike promotions, every table
here carries UUID id + `row_version` + tombstones from day one (§4.1). Wave M2
then wires: three catalogue-style apply branches (clone of the `category`
branch — reject-stale, delta-gated, tombstone-aware), emission at the config
CRUD sites, and `sale_item_modifiers` riding as an immutable
`ON CONFLICT(uid) DO NOTHING` type beside `sale_item` (its `uid` column and
partial index already exist). M2 is mechanical *because* M1 chose these ids;
the promotions tables, by contrast, cannot ever sync without an id retrofit.

### 4.9 The POS screen — the tap budget

The design constraint taken seriously: a cashier picking three modifiers per
item cannot take more taps than the sale is worth.

* **Defaults make the common case one tap.** If every required group on a
  product has an `is_default` option, a plain tap adds the line configured
  with defaults instantly — no picker, identical speed to today. The picker
  auto-opens only when a required group has no default (a genuine forced
  choice) — and that is the shop's own configuration decision, not a tax the
  feature imposes.
* **Explicit configure path:** long-press (desktop: right-click) the tile, or
  tap the cart line, opens the picker: groups stacked in `sort_order`,
  options as single-tap toggle chips (price delta on the chip), required
  groups first with the §4.5 disabled-Add rule. Worst realistic case: tap
  item + three chip taps + Add = five taps, on par with every restaurant POS
  in the market.
* **Cart model change (the real frontend work):** the merge key stops being
  `product_id` and becomes `product_id` + a canonical sorted
  modifier-selection signature — same selection twice merges to qty 2, plain
  vs modified stay separate lines. `_updateQty`, hold/resume (`held_sales`
  serializes the cart), `_recalc`'s mirror (unit price = base + deltas,
  display-only, server recomputes regardless), checkout payload
  (`modifier_option_ids` per item), and receipt rendering all follow that key.
  This — not the picker — is the largest single chunk of modifier work.

### 4.10 Reporting

* `top_products` stays exactly correct with zero change — Finding C. Modifier
  revenue attributes to the host product, which is the answer a menu report
  wants ("Burgers earned X as sold, configured").
* **Sales-by-modifier is new, small work:** a report over
  `sale_item_modifiers` snapshots joined to `sale_items → sales` for the
  period scope — option, times sold, delta revenue, cost. Same shape as any
  metrics.py aggregate; snapshots make it reprint-stable. Margin uses
  `cost_delta_snapshot` (§4.6) beside the existing current-cost caveat
  `top_products` already documents.

### 4.11 Invisible unless opted in

No groups configured ⇒ `product_modifier_groups` is empty ⇒ one indexed
probe per sale finds nothing (the v23 `load_active_promotions` precedent), no
line accepts selections (a payload naming options is refused — empty config
means nothing is attached to any product), the picker never opens, the cart
merge signature is empty ⇒ merge behaviour byte-identical, `create_return`'s
no-`sale_item_id` path is byte-identical, snapshots are never written, sync
payloads are unchanged. Cost to a non-adopter: one empty indexed lookup per
sale — the class of cost v23 already established as acceptable.

---

## 5. The neighbours — scoped, not designed

A restaurant is **not sellable on modifiers alone**. What else it needs, what
each depends on, and rough size (unit: one "variants wave" ≈ §3 in full):

**5.1 Kitchen tickets — depends on modifiers; ~0.5–0.75× variants.**
A ticket without modifier lines is useless, so this strictly follows
modifiers. The raw printing pipeline exists (client-side receipt rendering +
80 mm printer config, `subsystem-retail.js:3466–3481`); what's missing is
routing config (station/printer per category), a ticket document distinct
from the receipt (no prices, big modifier text, course/seat later), fire-on-
checkout hooks, and reprint. No new money paths, likely zero schema beyond a
routing settings blob. Multi-station routing over the network is where the
unknowns live.

**5.2 Split tender / tips — independent of modifiers; ~0.75–1× variants.**
Smaller than it looks on the ledger side: `payments` is already a rich
multi-row money ledger (`party_type/direction/method`, one funnel via
`_record_payment`, `retail_api.py:7362`, already a synced entity). The gap is
`create_sale`'s single `payment_method/amount_paid` contract, the POS payment
screen, and Z-report reconciliation by method. Tips are the genuinely new
concept (a column on `sales`, drawer/Z treatment, an owner decision on
tip-on-card handling — §8); tips touch cash-session variance, which is
Phase 4/16 territory and must be designed with the drawer, not around it.
Not needed for hypermarket; table stakes for restaurant.

**5.3 The sellability statement for the owner:** restaurant-ready =
modifiers (≈2–2.5) + kitchen tickets (≈0.5–0.75) + split tender/tips
(≈0.75–1) ≈ **3.5–4.5× the variants wave**, before any restaurant-specific
polish (tables/courses/seats — not even scoped here). Hold/resume already
covers basic tab-parking (`held_sales`, v11), which is why tables aren't on
the critical path for a counter-service restaurant pilot.

---

## 6. Sequencing — and the test of the owner's claim

**The claim ("hypermarket is the shorter path, restaurant the larger market")
survives on the path half, with a sharpening.** Verified against the code
rather than the mental model:

* **Variants complete a sellable story by themselves.** Apparel/hypermarket
  needs variants + what already exists and is solid: hardware barcode
  scanning, the v21/v22 scale+scan work, multi-branch, AR/AP, promotions,
  shift/cash drawer, sync. After §3, that vertical has no other blocking gap
  on this product's own missing-features list.
* **Modifiers do not complete a sellable story** (§5.3). The restaurant
  distance is not "the modifiers wave", it is 3.5–4.5 variants-equivalents.
* **Effort ratio:** variants ≈ 1 (two columns, one guard, a resolver tier, a
  picker, sync payload columns — no new money paths, returns untouched).
  Modifiers ≈ 2–2.5 (four tables, sale-path resolution + snapshots, a
  returns money-path change, the cart data-model rework, config admin UI,
  a new report). The single most expensive line item in modifiers is not the
  schema — it is §4.9's cart rework plus §4.4's returns change, both of which
  must be adversarially verified because they touch money.

**Recommendation: variants first**, as one self-contained wave (v25), shipped
and verified on a real catalogue, while the owner makes the §8 calls that gate
modifiers' shape. Then modifiers (v26) as wave M1, with M2 (config sync)
before the restaurant vertical is marketed to multi-till shops, and kitchen
tickets immediately after M1 (they depend on it). Split tender/tips can
overlap M2 — different files, different owner (disjoint-file-set rule).

The market-size half of the claim (restaurant > hypermarket in Jordan) is not
testable from this repo and is not contradicted by it; what the code adds is
the *price tag* on each market, which is the number the sequencing decision
actually needs.

---

## 7. Risks — how this goes wrong in a real shop

1. **The forgotten sync column (variants).** Payload or apply-list misses
   `parent_product_id` → peers silently show variants as standalone products.
   Named in §3.6 with a two-device acceptance test; do not skip it because
   single-device tests pass (the fixture-hides-the-bug shape).
2. **The parent guard's allow-half.** "Deny parents" mutated to "deny
   anything with a NULL parent" or similar denies every ordinary product —
   the exact deny/allow asymmetry ENGINEERING §1 warns on. Both directions
   mutation-proven in §3.3.
3. **Returns guessing between duplicate lines (modifiers).** The defect §4.4
   exists to close; if the line-addressing work is trimmed under pressure,
   the refuse-on-ambiguity guard (§4.4.2) is the minimum that may ship —
   a loud refusal beats a silent wrong-price refund.
4. **Snapshot re-read.** Any future code that recomputes a modifier line from
   live `modifier_options` instead of the snapshot rebuilds the exact bug
   `sale_item_promotions` was designed against. The snapshot columns'
   comments must carry the same "never re-read" language as v23's.
5. **Config drift across unsynced tills (wave M1).** Two tills, one edited
   menu → different prices for the same item until M2 lands. Money stays
   internally consistent per sale (server resolves against its own config,
   snapshots pin it), but the shop sees the discrepancy. Documented
   limitation in M1; the reason M2 is scheduled, not optional, for multi-till.
6. **Tap-budget regression.** If defaults are not seeded in the demo/config
   flow, every burger opens a picker and the till "feels slower" on day one —
   a perception failure that reads as a product failure. The config UI should
   nudge a default per required group at creation time.
7. **Version-number collision.** v24 is reserved-by-name for transfers; both
   claims here (v25/v26) go in ROADMAP.md **before** dispatch, per the
   single-writer rule that already burned this project once (schema-version
   collision precedent in MEMORY/ROADMAP).

---

## 8. Open questions — only the owner can answer these

1. **Is the restaurant vertical worth 3.5–4.5× the variants wave** before any
   restaurant revenue exists? This is the pricing/market call §6 cannot make;
   the code only prices the path.
2. **Multi-till restaurants at launch, or single-till pilot first?** Decides
   whether config sync (wave M2) must land *inside* the modifiers wave or may
   trail it. M1-then-M2 is the design's default; a multi-till launch collapses
   them into one.
3. **Variant creation UX for the hypermarket pitch:** is per-variant manual
   creation (reusing the existing product form, ~zero extra work) enough for
   the sales demo, or is a size-×-color matrix generator (the §3.1 option-
   matrix work, a wave of its own) needed to close deals? Affects wave 2
   scope, not wave 1 schema.
4. **Tips policy** (when §5.2 is scheduled): tip-on-card handling and whether
   tips enter the drawer variance calculation — a money-handling policy call
   that shapes the Z-report before any code is written.
5. **Do negative-delta modifiers ("no cheese −0.30") appear on the printed
   receipt as a visible reduction**, or fold silently into the unit price?
   Cosmetic, but it is the kind of receipt-format decision Jordanian shops
   have opinions about, and it should be his.
