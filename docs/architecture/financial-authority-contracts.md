# Financial Authority API Contracts (Wave 0, Part G)

Status: **PROVEN** — every field below is read directly from the current
route implementations (`products/retail/backend/api/retail_api.py`,
`products/clinic/backend/api/clinic_api.py`) as of the Wave 0 corrective
commits (`53911a9`, `8f315ab`, `57a3048`, `4f37e5d`). This document
describes the contract as implemented, not an aspiration.

## Purpose

Wave 0 made the backend the sole financial authority for Retail sales/
returns and Clinic payments (AUDIT-002/003/004/011/012). This document
freezes the *shape* of that authority's responses so future clients
(Windows, Android, any future integration) can rely on a stable contract
instead of re-deriving it from route source each time. A `calculation_version`
field is included specifically so a client can detect when the server's
rounding/discount/tax rules change underneath it.

Every field listed as "server-computed" is **never** taken from the
request body, even if the client supplies it — see each product's
correction doc for the specific fields a client may no longer set.

## Retail: `POST /api/sub/retail/sales`

### Client-supplied (commercial intent only)

| Field | Type | Notes |
|---|---|---|
| `items[].product_id` | int | required |
| `items[].quantity` | number | must be > 0; validated against live stock |
| `items[].discount_pct` | number | optional; clamped server-side to [0, 100] via `pricing.clamp_discount_pct()` |
| `branch_id` | int | optional; defaults to the company's default branch |
| `customer_id` | int | optional; required if the sale is effectively a credit sale |
| `payment_method` | string | tender method, e.g. `cash`/`card`/`credit` |
| `amount_paid` | number | tender amount; defaults to the server-computed `total` if omitted |
| `idempotency_key` | string (UUID recommended) | required for safe retry; company-scoped |
| `notes`, `due_date`, `cashier` | — | passthrough metadata, not financial |

Any of `unit_price`, `tax_rate`, `line_total`, `subtotal`, `discount_amount`,
`tax_amount`, `total` sent by the client are **ignored**. There is currently
no supported mechanism for an authorized price override; sending one has no
effect (not implemented — see `docs/corrections/wave0/retail-financial-authority-correction.md`).

### Server response (`data`)

| Field | Type | Source |
|---|---|---|
| `id` | int | sale row id |
| `sale_number` | string | server-generated, per-company sequence |
| `idempotency_key` | string \| null | echoed back |
| `currency` | string | company's `base_currency` setting |
| `subtotal` | number | sum of resolved line gross amounts, Decimal-quantized |
| `discount_amount` | number | sum of resolved line discounts |
| `tax_amount` | number | sum of resolved line tax |
| `total` | number | subtotal − discount + tax (mode-dependent; see `core/retail/pricing.py`) |
| `amount_paid` | number | tender actually recorded |
| `change` | number | `max(0, amount_paid − total)` |
| `balance_due` | number | `total − amount_paid` (drives AR credit logic) |
| `warning` | string \| null | e.g. soft credit-limit warning |
| `lines[]` | array | resolved per-line breakdown: `product_id, quantity, unit_price, discount_pct, tax_rate, line_total, branch_id` |
| `calculation_version` | string | `core.retail.pricing.CALCULATION_VERSION`, currently `"retail-pricing-v2-wave0"` |

Worked example (`subtotal=100, discount_pct=20%, tax=10%`):
`discount_amount=20.00, tax_amount=8.00, total=88.00` — verified by
`products/retail/tests/retail_financial_authority_test.py::test_worked_example_subtotal_100_discount_20pct_tax_10pct`.

## Retail: `POST /api/sub/retail/returns`

### Client-supplied

| Field | Type | Notes |
|---|---|---|
| `sale_id` | int | required; must belong to the caller's company |
| `items[].product_id` | int | must have been part of the referenced sale |
| `items[].quantity` | number | must be > 0 and not exceed what remains returnable for that sale+product |
| `reason`, `refund_method` | string | metadata |
| `idempotency_key` | string | required for safe retry; company-scoped |

`unit_price`/`discount_pct`/`tax_rate`/`line_total`, if sent, are ignored.
Refund figures are always recomputed from the **original** `sale_items` row.

### Server response (`data`)

| Field | Type | Source |
|---|---|---|
| `id` | int | return row id |
| `return_number` | string | server-generated (company-fragment-suffixed for global uniqueness) |
| `refund_amount` | number | tax-inclusive, proportional to quantity returned (see correction doc for the intentional semantics change from pre-Wave-0 tax-exclusive refunds) |
| `idempotency_key` | string \| null | echoed back |
| `items[]` | array | resolved per-line breakdown: `product_id, quantity, unit_price, discount_amount, tax_amount, line_total` |
| `calculation_version` | string | same versioning as the sale contract |

## Clinic: `POST /api/sub/clinic/payments`

### Client-supplied

| Field | Type | Notes |
|---|---|---|
| `invoice_id` | int | required; must belong to the caller's company |
| `amount` | number (Decimal-parseable) | required; must be `> 0` and not exceed the invoice's current outstanding balance |
| `method`, `reference` | string | metadata |
| `idempotency_key` | string | required for safe retry; company-scoped |

There is currently no customer-credit ledger, so an `amount` that would
overpay the invoice is rejected outright (400) rather than accepted as
account credit — see `docs/corrections/wave0/clinic-payment-correction.md`.
Only the invoice statuses the schema actually supports (`unpaid`, `partial`,
`paid`) are used; no additional status is invented by this contract.

### Server response (`data`)

| Field | Type | Source |
|---|---|---|
| `id` | int | payment row id |
| `invoice_status` | string | invoice status *after* this payment (`partial` or `paid`) |

On a duplicate `idempotency_key`, the server returns the **original**
payment's `id` and the invoice's *current* status rather than creating a
second payment row — total paid on the invoice never double-counts a
retried request.

## Platform consistency

Because the server derives `unit_price`/`tax_rate`/discount clamping and all
totals from server-side state (never the request body), an identical
commercial-intent payload (`product_id` + `quantity` + `discount_pct`)
produces an identical authoritative result regardless of which client sent
it — a "Windows request" and an "Android-style request" (historically
submitting `tax_rate: 0, discount_pct: 0` unconditionally) now converge on
the same `tax_amount`/`total` for the same product. Verified directly by
`retail_financial_authority_test.py::test_android_style_zero_tax_payload_still_computes_real_tax`
against the same fixture used by
`test_server_ignores_manipulated_totals_and_unit_price`. See
`docs/corrections/wave0/cross-platform-financial-validation.md` for the
full comparison table.

## Versioning

`calculation_version` (Retail) is the only explicit version marker today.
Clinic payments have no separate calculation engine to version (payment
validation is arithmetic comparison, not a pricing engine) — schema
compatibility for Clinic backups is tracked instead by
`commercial_runtime/backup/service.py`'s `SCHEMA_VERSION`. A future wave
that changes rounding/discount/tax rules must bump
`core.retail.pricing.CALCULATION_VERSION` and document the change here.
