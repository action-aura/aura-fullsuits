# Aura Retail — Financial Correctness Audit

Evidence class for every claim below: **PROVEN** (reproduced/read directly from
the exact executed code path) unless marked otherwise.

## THE KNOWN TAX-ON-DISCOUNT QUESTION — VERDICT: DISPROVEN (as a formula defect), but a SEPARATE and more severe defect makes the question moot for one platform

**Formula itself: correct, configurable, and defaults correctly.**
`products/retail/backend/core/retail/pricing.py` (lines 1-104) is a real, tested,
single-source-of-truth module implementing two explicit modes:

```
TAX_AFTER_DISCOUNT (default):  taxable = subtotal - discount; tax = taxable * rate
TAX_BEFORE_DISCOUNT:           taxable = subtotal (gross);    tax = taxable * rate
```

`DEFAULT_MODE = TAX_AFTER_DISCOUNT` (line 37) — tax is computed on the
**post-discount** amount by default, which is the financially-expected behavior.
`normalize_mode()` (line 40-43) safely falls back to the default for any
unrecognized/missing stored value, so a corrupted setting can't silently produce
an unintended tax basis. This is unit-tested directly:
`retail_pricing_test.py::test_tax_after_discount_mode_is_the_default`,
`test_before_and_after_discount_modes_diverge_when_discount_is_nonzero`, both
**PASS** (this audit, reproduced). **The formula is correct and the default is
correct.**

**But this formula is not actually the authority for what gets charged or persisted.**
`products/retail/backend/api/retail_api.py::create_sale()` (line 579) — the only
server-side route that writes a `sales` row — **never calls
`core/retail/pricing.py`**. It reads `subtotal`, `discount_amount`, `tax_amount`,
`total` directly from the client-submitted JSON body with no recomputation and no
cross-check against line items or product tax rates (lines 598-601):

```python
subtotal  = float(data.get('subtotal', 0))
discount  = float(data.get('discount_amount', 0))
tax       = float(data.get('tax_amount', 0))
total     = _money(data.get('total', 0))
```

So "is tax computed on pre- or post-discount amount" is answered differently
depending on **which client sent the request**, not by any server-enforced policy:

- **Windows/web POS** (`products/retail/frontend/subsystem-retail.js`, function
  `_recalc()`, lines 524-548): fetches the company's configured
  `tax_calculation_mode` from `GET /api/sub/retail/settings/tax` and reimplements
  the exact same two-mode formula in JavaScript, then submits the correctly
  computed `subtotal`/`discount_amount`/`tax_amount`/`total`. **Verified by
  reading the algebra line-by-line against `pricing.py` — it matches exactly for
  both modes.** So Windows sales are correctly taxed per the company's configured
  mode. **PROVEN correct, by source comparison** (not by an automated cross-check
  test — none exists, see `02`).
- **Android POS** (`android/aura-retail/.../ui/screens/RetailScreens.kt`, line 98):
  computes cart total as raw gross with **no tax, no discount, at all**:

  ```kotlin
  val total = cart.entries.sumOf { (id, qty) -> (byId[id]?.sell_price ?: 0.0) * qty }
  ```

  There is no discount-entry UI anywhere in the Android POS cart sheet (confirmed
  by reading the full `PosScreen` composable, lines 60-370 — search for any
  discount input control found none). The checkout call
  (`RetailScreens.kt` lines 344-355) submits:

  ```kotlin
  val saleTotal = total   // = raw gross, no tax, no discount
  ...
  ApiClient.get().createSale(CreateSaleRequest(
      subtotal = saleTotal, total = saleTotal, amount_paid = paidNow, ...))
  ```

  `CreateSaleRequest` (`net/Models.kt` line 179-184) declares
  `discount_amount: Double = 0.0, tax_amount: Double = 0.0` as defaults, and the
  call above never overrides them — so Android literally never sends a
  `discount_amount` or `tax_amount` field with a nonzero value, for any sale, ever.
  `SaleItemReq` (line 178) doesn't even have `tax_rate`/`discount_pct` fields to
  send per line. On the server, `item.get('discount_pct',0), item.get('tax_rate',0)`
  (line 660) then default those columns to `0` in `sale_items` too.

### Verdict, precisely

- The **tax-after-discount vs. before-discount** question, as asked, is
  **DISPROVEN as a live defect** — the formula that exists is correct and
  defaults correctly, and Windows correctly applies it.
- A **more severe, PROVEN defect exists specifically on Android**: every sale made
  through the Android Retail POS is recorded with **$0.00 tax and $0.00 discount**,
  regardless of the product's configured `tax_rate`, because the Android client
  (a) has no discount UI at all and (b) never computes or sends a tax figure, and
  (c) the server has no independent check to catch this. This is not an edge case
  — it is the unconditional behavior of every Android sale. Classified **P0** in
  the defect registry (`22`) — "financial totals fundamentally wrong across normal
  use," platform-specific to Android, and a genuine tax-compliance exposure for
  any company using Android as a POS terminal with taxable products.
- Underlying that, a **platform-independent architectural defect**: the server
  trusts 100% of client-submitted monetary fields for `create_sale()` with no
  server-side recomputation via `core/retail/pricing.py`, despite that module's own
  docstring claiming to be "the SINGLE SOURCE OF TRUTH." Any client — a modified
  Android build, a direct API call, a tampered browser session — can submit an
  arbitrary `total`, `tax_amount`, or `discount_amount` and have it persisted
  as-is. Classified **P1** ("common financial calculation error," here more
  precisely "no server-side financial validation exists at all") independent of
  the Android-specific consequence above.

## PRICING

- **Quantity/decimal quantities**: `pricing.calculate_line(unit_price, quantity, ...)` accepts `quantity` as a float with no validation of sign or magnitude — `gross = unit_price * quantity`. **Negative quantity is not rejected** by `pricing.py` itself; whether it's rejected upstream depends entirely on the caller (client UI), since (per above) `create_sale()` doesn't call `pricing.py` and doesn't validate `item['quantity']` sign either (line 655: `qty = float(item['quantity'])`, no `if qty <= 0` check). **PROVEN**: a client (any platform, or a raw API call) can submit a negative-quantity line item and it will be inserted into `sale_items` and applied to `inventory_movements`/`inventory_balances` as a stock **increase** (since the movement is `-qty`, a negative `qty` yields a positive inventory delta) disguised as a sale — a real stock-inflation vector, P2.
- **Zero-price products, large prices**: no upper/lower bound validation found anywhere in `create_sale`/`pricing.py`. Not exploitable for direct loss beyond what's already covered above (client controls the total anyway), but zero-price line items are accepted silently with no warning.
- **Rounding strategy is inconsistent within the same product**: `pricing.py`'s `calculate_line`/`calculate_invoice` round with plain Python `round(x, 2)` (banker's/round-half-to-even), while `retail_api.py::_money()` (line 962-966) uses `Decimal(...).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)`. These two rounding rules **disagree at exact half-cent boundaries** (e.g. a computed value of `x.xx5` rounds differently under round-half-even vs. round-half-up). Since `pricing.py` isn't actually invoked by `create_sale()` today (see above), this specific inconsistency is currently dormant/moot for sales, but would immediately matter the moment `pricing.py` is wired into `create_sale()` as a fix — flagged now so the eventual fix doesn't introduce a fresh rounding mismatch. P3.

## DISCOUNTS

- Line-level and invoice-level discount both exist in the web POS (`_recalc()`); Android has **neither**.
- **Discount > subtotal**: `pricing.py` does not clamp `discount_amount`/`discount_pct` to any range — a `discount_pct` of, say, 150 produces a negative `taxable_amount` and a negative `total`. No test exercises this. The web JS mirrors the same lack of clamping (`_recalc()`, no `Math.max`/`Math.min` guard on the discount input read at line ~338). This is unvalidated on both the Python and JS side; a legitimate-looking client input (a cashier fat-fingering "500" into a percent-discount field) can produce a negative sale total with no rejection anywhere in the pipeline. P2.
- **Compare to Clinic**, which explicitly clamps (`clinic_api.py` lines 699-700: `if discount < 0: discount = 0` / `if discount > subtotal: discount = subtotal`) — Retail has no equivalent guard anywhere. This is a real cross-product inconsistency (see `05`).

## TAX

Covered exhaustively above (mode selection, default, Android omission, server-trust gap). Additional items:

- **Return tax reversal**: `create_return()` (line 761) does not separately track a tax component at all — `refund = sum(item.line_total)` (line 771), a flat client-submitted figure with no tax/discount breakdown persisted per return. Whatever tax was or wasn't charged on the original sale is not explicitly reversed as "tax" — it's folded into one undifferentiated refund number. This makes any future tax-liability report (e.g. "total tax collected, net of tax refunded on returns") **impossible to compute correctly** from the `returns` table as it stands, since there is no `returns.tax_amount` field distinct from the refund total. P2 — reporting/compliance gap, not a same-transaction cash-handling error.

## SALES

- **Duplicate submission / repeated checkout click**: `create_sale()` DOES have idempotency protection — `idempotency_key` (lines 587-591): a repeat request with the same key returns the already-created sale instead of creating a second one. **PROVEN present and correct** for Retail sales (Android sends a fresh `UUID.randomUUID()` per checkout attempt — `RetailScreens.kt` line 355 — so this only protects against true network-level retries of the *same* logical attempt, not a genuine double-tap that generates two different UUIDs; a fast double-tap of the Charge button before `charging = true` visually disables it would still generate two distinct idempotency keys and create two real sales. The Kotlin code does set `charging = true` synchronously before the async call, which should prevent a same-composition double-invoke, but this was not exercised on a real device/touchscreen — UNVERIFIED for actual double-tap timing).
- **Underpayment/overpayment**: `change = max(0.0, _money(paid - total))` (line 603) and `balance_due = _money(total - paid)` (line 607) — an overpayment produces `change` correctly and a negative `balance_due` is possible but not specially handled (falls through to `is_credit = (pm=='credit') or (balance_due > 0.005)` — a negative balance_due is not `> 0.005`, so overpayment doesn't spuriously trigger credit logic; looks correct on inspection). **No explicit test** exercises overpayment amounts (see `02`).
- **Invoice numbering**: `sale_number = _next_ref(conn, cid, 'sale')` — not shown in the excerpt read, assumed to be a company-scoped sequential/atomic allocator; not independently audited in this pass (time-boxed) — UNVERIFIED for race-safety under truly concurrent requests (SQLite's own locking would serialize the transaction either way, given `create_sale` runs inside `BEGIN TRANSACTION`).
- **Restart persistence**: covered by the real Windows smoke test (`docs/build/retail-windows-build-report.md`, step 7) — data survived a `taskkill`+reopen. **PROVEN** for Windows. UNVERIFIED for Android (no device).

## RETURNS AND REFUNDS

`create_return()` (`retail_api.py` line 761-805) — **PROVEN, by direct reading, to have no linkage validation to the original sale at all**:

- `data.get('sale_id')` is stored on the `returns` row but **never checked to exist**, never checked to belong to the current company, and never used to look up what was actually sold.
- **Return quantity greater than sold**: not checked — `qty = float(item['quantity'])` (line 786) is applied to inventory with no comparison against `sale_items.quantity` for the referenced sale.
- **Repeated return of the same quantity**: not checked — nothing prevents submitting the identical return payload N times; each submission independently credits `refund_amount` (client-controlled per-item `line_total`) and increments `inventory_balances` again.
- **Return without an original sale**: `sale_id` can be `null`/omitted/nonexistent and the return still processes successfully.
- **Refund rounding, tax reversal, discount reversal**: not modeled — see TAX section above (folded into one number).

**Classified P0** — "financial loss," and specifically the audit-defined P0 example
"unrecoverable data corruption"/uncontrolled financial leakage class, since this
is a repeatable, self-service refund-and-restock exploit requiring only POS access
(available to any cashier role on either platform) with no manager override,
approval step, or original-sale cross-check anywhere in the path. This is
**worse** than a simple calculation bug because it is a direct cash-out vector,
not just a reporting error.

## REVENUE AND DASHBOARD

- Returns are netted from gross sales by date, using `COALESCE(SUM(refund_amount),0)` grouped by `date(created_at)` (lines 90-95 excerpt) — structurally consistent with "net revenue = sales − returns," matching the behavior the project's own memory/prior-phase notes claim was tested (decimal precision, negative net revenue, zero-denominator percentage guards — all in `retail_pricing_test.py`, confirmed **PASS** in isolation this audit). Not independently re-derived from first principles in this pass beyond re-running the existing tests — this section defers to `02`'s confirmation that those specific tests pass, and does not claim additional new verification of the dashboard math beyond what those tests already assert.
- Given the return-validation gap above, the dashboard's "net revenue" figure is **only as trustworthy as the input** — a fraudulently repeated return still nets out of revenue "correctly" arithmetic-wise while being fraudulent in substance. The dashboard math is not the defect; the unvalidated write path feeding it is.

## INVENTORY FINANCIAL IMPACT

- Stock decrease on sale / increase on return: both present, both happen inside the same DB transaction as the financial write (good — no separate inventory-only transaction that could drift from the financial record).
- **Negative stock**: `addOne()` in Android's Kotlin UI (`RetailScreens.kt` line 103-108) client-side-guards against exceeding `p.total_stock`, but **the server itself does not** — `create_sale()` never checks `inventory_balances.quantity_on_hand` before decrementing (line 665-668: unconditional `UPDATE ... SET quantity_on_hand = quantity_on_hand - ?`). A direct API call (or a race between two near-simultaneous legitimate sales of the last unit) can drive `quantity_on_hand` negative. P2 — data-integrity issue with financial consequence (negative stock corrupts any margin/valuation report that multiplies quantity by cost).
- No cost-price/margin/profit calculation was found in `retail_api.py`'s sale path (`sell_price` only) — `cost_price` exists as a product column but isn't referenced by any of the routes read in this pass; margin/profit reporting, if it exists, was not located and is treated as **NOT PRESENT** rather than defective.

## Summary table

| Question | Answer |
|---|---|
| Tax-on-discount formula defect? | **DISPROVEN** — formula correct, defaults correctly (after-discount) |
| Tax-on-discount actually applied on Android? | **PROVEN NOT APPLIED — $0 tax on every Android sale** |
| Server validates client-submitted totals? | **PROVEN NO** — full client trust on `create_sale` |
| Returns validated against original sale? | **PROVEN NO** — no linkage, quantity, or duplicate check |
| Discount clamped to a sane range? | **PROVEN NO** (Retail); Clinic does this correctly (see `04`) |
| Negative stock preventable server-side? | **PROVEN NO** |
| Rounding strategy consistent? | **PROVEN NO** (dormant inconsistency, would surface if `pricing.py` is wired in) |
