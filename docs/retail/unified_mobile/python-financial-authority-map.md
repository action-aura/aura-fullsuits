# Aura Retail Unified Mobile — Python Financial Authority Map (M3.0)

Real code audit of the Retail Python backend's financial and transaction authority, traced through routes, the pricing engine, models, and tests — not inferred from route names.

## The single canonical calculation authority

`products/retail/backend/core/retail/pricing.py` — its own module docstring states it explicitly: *"SINGLE SOURCE OF TRUTH for how tax interacts with discount on a sale, AND the only module allowed to compute a persisted financial total anywhere in this backend."* Two functions:

### `calculate_line(unit_price, quantity, discount_pct=0, tax_rate=0, mode=DEFAULT_MODE) -> dict`

Per-line calculation, the authority `create_sale()` and `create_return()` both call for every line. Two tax modes, selected per-company (`retail_settings.tax_calculation_mode`):

| Mode | Formula |
|---|---|
| `TAX_AFTER_DISCOUNT` (default) | `taxable_amount = gross - discount_amount`; `tax = taxable_amount * rate`; `total = taxable_amount + tax` |
| `TAX_BEFORE_DISCOUNT` | `taxable_amount = gross` (tax ignores discount); `tax = gross * rate`; `total = gross - discount_amount + tax` |

Where `gross = unit_price * quantity`, `discount_amount = gross * (discount_pct / 100)`. `discount_pct` is clamped to `[0, 100]` via `clamp_discount_pct()` before use — negative becomes 0, over-100 becomes 100, malformed becomes 0 (never raises). An unrecognized/missing `mode` silently falls back to `DEFAULT_MODE` (`normalize_mode()`) rather than raising.

**Rounding**: every one of `gross`/`discount_amount`/`taxable_amount`/`tax`/`total` is independently rounded to 2 decimal places via `Decimal.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)` before being returned as a `float`. All arithmetic runs in `Decimal` internally (`_d()` converts via `str()` first, specifically to avoid `Decimal(0.1) != Decimal('0.1')` binary-float artifacts) — real, deliberate, documented (Wave 0 / AUDIT-006 correction from Python's built-in banker's-rounding `round()`).

### `calculate_invoice(subtotal, discount_amount, tax_rate_pct, mode) -> dict`

Cart/invoice-level variant: `discount_amount` is already a **currency amount**, not a percentage (a different discount model than per-line `discount_pct` — see "Two distinct discount models" below). Clamped to `[0, subtotal]`. Same two tax-mode formulas, applied once to the summed subtotal rather than per line. Test-proven equivalent to summing `calculate_line()` results for a single line (`retail_pricing_test.py:114-119`).

### Two distinct discount models (real finding, must both be represented in the shared command model)

1. **Per-line percentage discount** (`sale_items.discount_pct`, `calculate_line`'s `discount_pct` param) — what `create_sale()` actually persists per line.
2. **Cart/invoice-level currency-amount discount** (`calculate_invoice`'s `discount_amount` param) — a different shape, used where a caller has already summed lines into one subtotal. Not currently invoked by any route in `retail_api.py` (no caller found) — exists as a public, tested API of `pricing.py` but is not load-bearing for any persisted sale today. Real finding for M3.3's `ApplyOrderDiscount` command: the Python authority already anticipates an order-level discount shape distinct from per-line discounts, giving a real behavioral reference for that command, even though nothing currently calls it.

## Sale finalization — `POST /api/sub/retail/sales` (`create_sale()`, `retail_api.py:614-818`)

**Server-authoritative** (own docstring, AUDIT-002/AUDIT-003): client may only send commercial intent — `product_id`+`quantity` (+optional `discount_pct`) per line, `payment_method`/`amount_paid`, `customer_id`, `branch_id`, `idempotency_key`. **`unit_price`, `tax_rate`, `line_total`, `subtotal`, `discount_amount`, `tax_amount`, `total` are silently ignored if the client sends them** — always resolved server-side from the `products` table and `pricing.calculate_line()`.

**Idempotency** (`retail_api.py:635-639`): if `idempotency_key` is present, look up `sales` by that exact key **before any mutation**; if found, return the existing `{id, sale_number}` immediately. **Real gap, not a port target**: there is no comparison of the retried payload against the original — a second call with the *same key but different items* silently returns the *first* call's result rather than raising a conflict. The governing spec's required `DUPLICATE_OPERATION_CONFLICT` behavior ("conflicting idempotent payload returns conflict") **does not exist in the current Python authority**. This is a `CANONICAL_UNIFIED_RULES` item (M3.6), not a `LEGACY_PARITY_RULES` port target.

**Transaction boundary**: `BEGIN IMMEDIATE` taken before any line resolution (own comment: prevents a concurrent sale from reading the same "stock is sufficient" snapshot before either commits — the real, existing oversell-race mitigation, AUDIT-009). Every per-line validation failure does `conn.rollback(); conn.close()` and returns a 400 immediately — no partial sale is ever written for a rejected line.

**Per-line validation, in order**:
1. Product must exist for this company (`RECORD_NOT_FOUND`-equivalent, 400 "Product {pid} not found").
2. Product must be `status == 'active'` (`INACTIVE`-equivalent, 400).
3. `quantity = float(item.get('quantity'))` — `TypeError`/`ValueError` caught -> `INVALID_QUANTITY`-equivalent, 400 "Invalid quantity." **Real defect** (not a parity target — see `financial-invariant-catalog.md`): Python's `float()` accepts `"nan"`, `"inf"`, `"-inf"`, and scientific notation (`"1e5"`) without raising. `nan <= 0` and `nan > 0` are both `False` in IEEE-754, so a `NaN` quantity **passes the next check silently**.
4. `quantity <= 0` -> `NON_POSITIVE_QUANTITY`-equivalent, 400 "Quantity must be greater than zero." (NaN slips through this check too, per above.)
5. Stock check: `quantity > on_hand` (0 if no balance row) -> `INSUFFICIENT_STOCK`-equivalent, 400, includes have/requested in the message. (`float('inf')` would fail here correctly, since `inf > on_hand` for any finite `on_hand`.)
6. `discount_pct` clamped via `pricing.clamp_discount_pct()` (never rejected, only clamped — no `DISCOUNT_EXCEEDS_LIMIT` error exists in the current authority; clamping is the real, current behavior).

**Payment/change** (`retail_api.py:717-724`): `paid = _money(amount_paid or total)` (defaults to exactly `total` if omitted — a sale with no `amount_paid` is treated as fully paid). `change = max(0, paid - total)`. **Real finding**: payment below total is **not rejected** — `PAYMENT_BELOW_TOTAL` as a hard error does not exist. Instead, `balance_due = total - paid`; if `balance_due > 0.005` the sale is treated as a credit sale, which **requires** a `customer_id` (walk-in credit sales are rejected: "Credit sales require a customer (walk-in not allowed)."). So the real, precise rule is: *underpayment is allowed only for a named customer, and becomes accounts-receivable debt, not a rejected transaction.*

**Credit-sale rules** (`retail_api.py:725-748`): per-customer `credit_mode` (`none`/`limited`/unlimited), `credit_limit`, running `credit_balance`. `credit_mode == 'none'` -> hard reject. `credit_mode == 'limited'` and `(current_balance + this_sale's_balance_due) > limit + 0.005` -> either hard reject (`enforce_credit_limit == 'block'`) or a non-blocking `warning` string returned alongside success. This is AR-domain policy layered on top of the pure financial engine, not part of `calculate_line`/`calculate_invoice` themselves — represented as a separate shared service concern (M3.4), not folded into the pure calculation engine (M3.2).

**Persistence** (single transaction, `retail_api.py:750-786`): one `sales` row (status always `'completed'` — there is no draft/pending sale state in the current schema), one `sale_items` row per line (persisting the resolved `unit_price`, `discount_pct`, `tax_rate`, `line_total` — this is the historical snapshot data), one `inventory_movements` row per line (`movement_type='sale_out'`, negative quantity), one `inventory_balances` decrement per line. Customer `total_spent`/`loyalty_points` updated if a customer is attached (1 point per $10 spent, real, exact, currently undocumented-elsewhere formula). Payment ledger entry (`_record_payment`) if `paid > 0.005`; AR credit adjustment (`_adjust_credit`) if a credit balance remains.

**Sale-item snapshot fields** (real schema, `sale_items` table): `sale_id, product_id, quantity, unit_price, discount_pct, tax_rate, line_total` — this is exactly the set of fields that must survive in the shared `FinalizedSaleLineSnapshot` (M3.3) so a receipt can be reconstructed even if the live product's name/price/tax changes later. **Real finding**: the product **name** is NOT snapshotted onto `sale_items` — it is joined live from `products.name` at read time (confirmed: no `product_name` column in the `sale_items` schema, `list_products`-style joins are the only place a name is resolved). This means **the current Python authority does NOT actually satisfy the governing spec's own snapshot requirement** ("even if product name changes... do not rely on live product values when rendering an old receipt") for the product name field specifically. This is a real, documented Python limitation — a `CANONICAL_UNIFIED_RULES` item for M3.6, not a parity target to silently reproduce.

## Return/refund finalization — `POST /api/sub/retail/returns` (`create_return()`, `retail_api.py:875-1023`)

**Server-authoritative** (AUDIT-004): client sends only `sale_id` + `{product_id, quantity}` per line. Refund figures are **always recomputed from the original `sale_items` row** for that sale+product (`unit_price`, `discount_pct`, `tax_rate` as they were at sale time — real, working snapshot-based refund, unlike the sale side's missing product-name snapshot), proportional to the quantity being returned, via the same `pricing.calculate_line()`. This is real, already-correct historical-value-derivation for the refund amount itself (though not for the product's display name).

**Idempotency**: same shape as sales, company-id-scoped this time (`retail_api.py:900-906`, with an explicit comment about why: an unscoped lookup would let one company guess another's idempotency key). Same real gap: no conflicting-payload detection.

**Transaction boundary**: `BEGIN IMMEDIATE` (own comment: "two returns against the same sale+product racing each other must not both read the same 'remaining returnable' snapshot").

**Per-line validation, in order**:
1. `sale_id` must resolve to a real sale for this company -> 404 "Original sale not found." (checked *before* the transaction lock is taken — real minor TOCTOU note, not exploitable for the returns case since the lock is taken immediately after for the actual quantity check).
2. `quantity = float(...)` — same NaN-via-`float()` defect as sales.
3. `quantity <= 0` -> reject. (Same NaN-slips-through issue.)
4. Product must have actually been part of the original sale (`sale_items` lookup) -> 400 if not found.
5. **Cumulative-return enforcement**: `already_returned = SUM(return_items.quantity)` across every prior return referencing this `sale_id`+`product_id`; `remaining = sold.quantity - already_returned`; `quantity > remaining + 0.0001` (real epsilon-tolerance for float comparison) -> reject with an exact have/requested/already-returned message. This is the real, working, cumulative-across-multiple-returns invariant the governing spec requires ("cumulative returns cannot exceed original quantity") — already correct in Python, a genuine `LEGACY_PARITY_RULES` port target, not a gap.

**Persistence**: one `returns` row (`status` always `'completed'`, `refund_amount` = sum of all lines' `calculate_line().total`), one `return_items` row per line, `inventory_balances` incremented (stock restored), `inventory_movements` row per line (`movement_type='return_in'`, positive quantity), one audit-log entry.

## Product/stock authority referenced by the above

`retail_api.py:224-365` (`list_products`/`create_product`/`update_product`/`delete_product`/`stock-adjust`) — `products.sell_price`/`tax_rate` are the live values `create_sale()` resolves per line (not client-supplied). `delete_product` soft-deletes (`status='inactive'`) if the product has any sales history, hard-deletes only if it has none — the real archival pattern referenced in `complete-retail-capability-matrix.md`'s Categories/Branches discussion.

## Test authority cross-reference (real vectors, used to build M3.5's fixtures)

`products/retail/tests/retail_pricing_test.py` (436 lines, Part A = pure `pricing.py` unit tests, Part B = HTTP-level sale/return/dashboard end-to-end): 25 real test functions covering both tax modes, mode-divergence-only-when-discount-nonzero, unknown-mode fallback, multi-quantity, float-precision rounding, zero-tax, `calculate_invoice`/`calculate_line` equivalence, partial/full/multi-line returns, and dashboard revenue-not-clamped-negative. Concrete vectors extracted (real, exact, from the file):

- `calculate_line(100, 1, 0, 15, AFTER)` -> `taxable=100.0, tax=15.0, total=115.0`
- `calculate_line(100, 1, 10, 15, AFTER)` -> `discount=10.0, taxable=90.0, tax=13.5, total=103.5`
- `calculate_line(100, 1, 10, 15, BEFORE)` -> `taxable=100.0, tax=15.0, discount=10.0, total=105.0`
- `calculate_line(19.99, 3, 5, 8, AFTER)` -> `gross=59.97`, `total=round(round(59.97-2.9985,2)*1.08,2)` (real rounding-order reference)
- `calculate_line(50, 2, 10, 0, AFTER)` -> `tax=0.0, total=taxable_amount`

`retail_financial_authority_test.py` (250 lines), `wave1c_financial_gate_test.py` (264 lines), `retail_returns_wave0_test.py` (262 lines) — HTTP-level server-authoritative-total / client-value-ignored / return-quantity-limit assertions; full per-test enumeration deferred to `financial-invariant-catalog.md` (which maps each real invariant, not each test file).
