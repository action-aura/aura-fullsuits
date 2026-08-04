# Import Handler Matrix (M5.8.0)

Structured, field-by-field matrix of the 5 real entity handlers audited
in `import-authority-audit.md`, for direct reference while designing the
shared Kotlin contracts (M5.8.1) and entity-detection logic (M5.8.9).

## Products (`_handle_retail_products`)

| Field | Required | Type | Legacy aliases (sample) | Default if absent |
|---|---|---|---|---|
| `name` | yes | text | productname, itemname, articlename, title | — |
| `sku` | yes | text | itemcode, productcode, code, partnumber, sku | row skipped if absent |
| `barcode` | no | text | upc, ean, gtin, isbn | `''` |
| `category` | no | text | productgroup, itemgroup, category | none (no category link) |
| `cost_price` | no | number | cost, costprice, buyprice, wholesaleprice | `0` |
| `sell_price` | yes | number | price, sellprice, retailprice, mrp | — |
| `tax_rate` | no | number | taxrate, vat, gst | `0` |
| `unit` | no | text | uom, unitofmeasure, packtype | `'pcs'` |
| `reorder_level` | no | integer | reorder, minstock, safetystock | `5` |
| `initial_stock` | no | number | qty, quantity, stock, onhand | not set (no inventory row) |

- **Dependency**: `category` (auto-created by name if missing, real
  existing behavior), one Branch (auto-creates `"Main Store"` if the
  company has zero branches, real existing behavior).
- **Duplicate key**: `(company_id, sku)` → `UPDATE_EXISTING`.
- **No-SKU behavior**: `SKIP` (counted, not rejected as a hard error).
- **Inventory**: absolute-set (`UPDATE ... SET quantity_on_hand=?`), not
  additive — a second import of the same file with a different
  `initial_stock` value OVERWRITES the on-hand quantity, it does not add
  to it.

## Customers (`_handle_retail_customers`)

| Field | Required | Type | Legacy aliases (sample) | Default if absent |
|---|---|---|---|---|
| `name` | yes | text | fullname, customername, contact | row skipped if absent |
| `phone` | no | text | mobile, tel, cell | `''` |
| `email` | no | email | emailaddress, mail | `''` |
| `address` | no | text | streetaddress, mailingaddress | `''` |
| `loyalty_points` | no | number | points, rewardpoints | `0` |
| `total_spent` | no | number | totalspent, lifetimevalue | `0` |

- **Dependency**: none.
- **Duplicate key**: lower-cased `email`, only when non-empty →
  `UPDATE_EXISTING`; blank email → always `INSERT` (real, disclosed gap
  — no fallback dedup key).

## Suppliers (`_handle_retail_suppliers`)

| Field | Required | Type | Legacy aliases (sample) | Default if absent |
|---|---|---|---|---|
| `name` | yes | text | companyname (via generic `name` aliases) | row skipped if absent |
| `phone` | no | text | same as customers | `''` |
| `email` | no | email | same as customers | `''` |
| `address` | no | text | same as customers | `''` |

- **Dependency**: none.
- **Duplicate key**: case-sensitive exact `name` → `SKIP` (never
  updated, unlike products/customers).

## Branches (`_handle_retail_branches`)

| Field | Required | Type | Legacy aliases (sample) | Default if absent |
|---|---|---|---|---|
| `name` | yes | text | branchname (via generic `name` aliases) | row skipped if absent |
| `address` | no | text | — | `''` |
| `phone` | no | text | — | `''` |
| `status` | no | text | state, flag, isactive | `'active'` (lower-cased) |

- **Dependency**: none.
- **Duplicate key**: case-sensitive exact `name` → `SKIP`.

## Categories (`_handle_retail_categories`)

| Field | Required | Type | Legacy aliases (sample) | Default if absent |
|---|---|---|---|---|
| `name` | yes | text | categoryname, productgroup, catname | row skipped if absent |
| `description` | no | text | memo, narration, remarks | `''` |

- **Dependency**: none.
- **Duplicate key**: case-sensitive exact `name` → `SKIP`.

## Real dependency graph (confirmed from `_ENTITY_ORDER`)

```
categories ─┐
suppliers ──┼─→ (no real FK from these three to anything else)
branches ───┘
customers      (no real FK dependency on any other entity)
products ──────→ categories (category_id FK, auto-created if missing)
products ──────→ branches (initial_stock write targets one Branch, auto-created if none exists)
```

Only `products` has real foreign-key dependencies (`category_id`, and
implicitly one Branch for `inventory_balances`). `customers`'s ordering
before `products` in `_ENTITY_ORDER` is conventional, not FK-driven.

## Real, existing status/archive semantics carried into this matrix

- `branches.status` accepts free text, defaulted to `'active'` — no
  enum/CHECK constraint at the import layer (matches the same real gap
  M5.5's own `product-inventory-authority-audit.md` found for
  `inventory_movements.movement_type` — free text, no enforced vocabulary
  — a recurring legacy pattern, not unique to import).
- No `is_active`/archived-status handling exists for
  `products`/`customers`/`suppliers`/`categories` import — only `branches`
  accepts a `status` column at all.

## Case-sensitivity inconsistency (carried from `import-authority-audit.md`)

| Handler | Match basis | Case handling |
|---|---|---|
| products | `sku` | exact (SKUs are case-significant by nature) |
| customers | `email` | lower-cased |
| suppliers | `name` | **case-sensitive**, unnormalized |
| branches | `name` | **case-sensitive**, unnormalized |
| categories | `name` | **case-sensitive**, unnormalized |

This real inconsistency is the concrete input to
`import-duplicate-conflict-policy.md`'s decision — reuse the M5.5
`CatalogImporter`/`UnicodeTextNormalizer` normalization discipline
(`product-domain-contract.md`) for the shared Kotlin authority rather
than replicate the case-sensitive legacy behavior for
suppliers/branches/categories, since the checkpoint explicitly forbids a
second duplicate policy and the M5.5 authority already normalizes
Category/Branch names consistently.
