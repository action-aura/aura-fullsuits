# Wave 0 Correction — Retail Returns (AUDIT-004)

Status: **FIXED AND VERIFIED**

## Root cause

`create_return()` never validated that `sale_id` referred to a real sale
belonging to the caller's company, never checked whether the requested
quantity had actually been sold (or how much of it had already been
returned), and computed `refund_amount` purely from client-submitted
`line_total` values. Its only accidental protection against duplicate
submission was a `return_number = f'RET-{int(time.time())}'` collision
within the same second — not a real idempotency guard.

## What was fixed

- `sale_id` is looked up as `(id, company_id)`; a missing or foreign sale
  returns `404` before any write.
- For each returned line, the corresponding `sale_items` row for
  `(sale_id, product_id)` must exist (`400` "was not part of this sale"
  otherwise).
- The quantity already returned against that sale+product is summed from
  `return_items JOIN returns` (company-scoped), and the requested quantity
  may not exceed `sold.quantity - already_returned` (`400` otherwise) —
  this makes cumulative partial returns correctly bounded.
- Refund is recomputed server-side from the **original** sale line's
  `unit_price`/`discount_pct`/`tax_rate` via the same
  `pricing.calculate_line()` used for sales, proportional to the quantity
  being returned — so return math can never drift from sale math.
- A real `idempotency_key` (new company-scoped-unique DB column) collapses
  a retried submission into the original return instead of creating a
  second refund.
- `BEGIN IMMEDIATE` acquires the write lock up front so two returns racing
  against the same sale+product cannot both read the same "remaining
  returnable" snapshot.

## Intentional behavior change: `refund_amount` is now tax-inclusive

Pre-Wave-0 tests asserted `refund_amount` equal to the pre-tax taxable
amount only (e.g. `100.0` for a $100 item with 15% tax). That is financially
wrong: a return should refund the tax the customer actually paid, not just
the taxable base. `sale_items.line_total`'s existing meaning (post-discount,
pre-tax "taxable amount," consumed elsewhere by the margin/profit report:
`SUM(si.line_total) as revenue`) was deliberately preserved — only the
*return's own* `refund_amount` field changed to be tax-inclusive, since a
refund and a revenue-recognition figure are not the same thing and should
not share a formula.

Five existing assertions in `retail_pricing_test.py` were updated to match
(each with a comment citing AUDIT-004), not deleted:
`test_partial_return` (`100.0` → `115.0`), `test_full_return` and
`test_multi_line_return` (`200.0` → `230.0`),
`test_revenue_becoming_negative_is_not_clamped`, and
`test_dashboard_breakdown_internally_consistent` (`100.0` → `115.0`).

## A collision bug found and fixed while adding real return numbers

Switching `return_number` from the accidental `time.time()` scheme to a
real per-company sequence (`_next_ref`) exposed a latent bug:
`returns.return_number` carries a bare, non-company-scoped `UNIQUE`
constraint, but `_next_ref`'s counter resets to `1` for every new company —
so two different companies' first return would both generate
`"RET-000001"` and collide in this shared multi-tenant database. Fixed by
appending a company-ID fragment (`f"{_next_ref(...)}-{str(cid)[:8]}"`),
scoped only to `create_return` — the shared `_next_ref` helper and other
doc types (`sale`, `po`, `receipt`) were not touched, to stay within Wave
0's scope. `sales.sale_number` has the identical latent flaw but it is
outside this correction's scope (see the residual risk register).

## Tests

`products/retail/tests/retail_returns_wave0_test.py` (10 tests): full
return, partial return, cumulative partial returns (third rejected),
excessive return rejected up front, return against a nonexistent sale
(404), return of a product not in the sale (400), duplicate
`idempotency_key` collapses to one return, multi-line return sums
correctly, inventory restoration, and discount+tax proportional reversal.

## Commit

`53911a9` — feat: centralize retail authoritative financial calculations
(bundled with the sale rewrite since both were edited before the first
commit of this phase).

## Residual risk

`sales.sale_number`'s identical bare-UNIQUE-vs-per-company-sequence flaw is
not fixed here (out of Wave 0 scope; masked in existing tests by an
explicit random-seed workaround in `_make_admin_and_client()`/equivalent
helpers). Tracked in the residual risk register for a future wave.
