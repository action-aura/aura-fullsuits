# Wave 0 Correction — Retail Financial Authority (AUDIT-002/003/005/006/008/009)

Status: **FIXED AND VERIFIED**

## Root cause

`create_sale()` trusted client-submitted `unit_price`, `tax_rate`,
`line_total`, `subtotal`, `discount_amount`, `tax_amount`, and `total`
outright. This is a single root cause with two proven symptoms: Android's
client always sent `tax_amount: 0, discount_amount: 0` (a client bug), and
*any* client — Android or otherwise — could submit an arbitrary, internally
inconsistent total (a server bug). Fixing the server's trust boundary
resolves both, which is why AUDIT-002 and AUDIT-003 are fixed together.

`core/retail/pricing.py` also used Python's binary-float arithmetic (subject
to representation error) and built-in `round()` (banker's rounding, not the
half-up convention `_money()` elsewhere in the codebase already used), and
did not clamp `discount_pct` to a sane range.

## What was fixed

### `core/retail/pricing.py`
- Rewritten to compute internally in `Decimal` (`_d()` converts via `str()`
  to avoid binary-float artifacts) and round with `ROUND_HALF_UP` via
  `_money()`, matching the codebase's existing money-rounding convention.
- Added `clamp_discount_pct()`: clamps to `[0, 100]`; malformed input
  becomes `0` rather than raising. `calculate_invoice()`'s `discount_amount`
  is likewise clamped to `[0, subtotal]`.
- Public function signatures and return shapes are unchanged (still plain
  `float` dicts) — existing callers and all 26 pre-Wave-0 pricing unit tests
  pass unmodified against the rewritten module.
- `CALCULATION_VERSION = "retail-pricing-v2-wave0"` added and returned in
  every sale/return response (Part G contract).

### `products/retail/backend/api/retail_api.py` — `create_sale()`
Rewritten to be server-authoritative:
- `unit_price` and `tax_rate` are **always** resolved from the `products`
  table by `(product_id, company_id)`, never the request body.
- `discount_pct` is the only per-line commercial input accepted, and it is
  clamped via `pricing.clamp_discount_pct()`.
- `quantity` is validated as numeric and `> 0` (AUDIT-008); non-numeric or
  `<= 0` quantity is rejected with 400 before any write.
- Live stock (`inventory_balances.quantity_on_hand`) is checked against the
  requested quantity inside a `BEGIN IMMEDIATE` transaction (AUDIT-009) —
  the write lock is acquired before the check, so two concurrent sales for
  the last unit of stock cannot both read the same pre-decrement balance.
- `subtotal`/`discount_amount`/`tax_amount`/`total` are accumulated as
  `Decimal` across all resolved lines, then re-quantized once at the end.
- An unknown or inactive (`status != 'active'`) product is rejected (400)
  rather than silently priced at whatever the client claims.
- The response now includes `subtotal`, `discount_amount`, `tax_amount`,
  the resolved `lines[]` breakdown, and `calculation_version` — see
  `docs/architecture/financial-authority-contracts.md`.
- `amount_paid` (tender) remains legitimate client input, applied only
  *after* the authoritative `total` is computed.
- **Deliberately not implemented**: an authorized price-override field.
  The spec allows one only "when business rules explicitly permit," and no
  such authorization mechanism currently exists in this codebase — adding
  one would be scope expansion beyond Wave 0, so client-submitted price
  overrides continue to have no effect (silently ignored, matching every
  other client-submitted financial field).

## Worked example (verified by test)

`unit_price=100, quantity=1, discount_pct=20%, tax_rate=10%` (after-discount
mode, the default): `discount_amount=20.00`, taxable base `=80.00`,
`tax_amount=8.00`, `total=88.00`.

## Tests

`products/retail/tests/retail_financial_authority_test.py` (10 tests):
manipulated-total rejection (server recomputes instead), Android-style
zero-tax payload now taxed correctly, the worked example above, discount
clamped above 100% and below 0%, zero/negative quantity rejected,
insufficient stock rejected without touching inventory, unknown/foreign
product rejected, duplicate `idempotency_key` collapses to the original
sale (stock decremented exactly once), and stock decrements by exactly the
quantity sold.

## Commits

`53911a9` (create_sale rewrite + pricing.py rewrite), `57a3048` (FK
enforcement bundled in the same wave).

## Residual risk

The negative-stock policy is enforced only as "reject the sale if requested
quantity exceeds on-hand stock" (a transactional oversell guard) — there is
no separate configured business rule in this codebase for negative-stock
tolerance (e.g. backorder support), so none was invented; this satisfies the
spec's "at minimum prevent accidental oversell via transactional check."
