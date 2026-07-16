# Cross-Platform Financial Parity — Windows vs. Android

## Structural note (applies to both products)

There is exactly **one** backend implementation per product — Android does not
reimplement `retail_api.py`/`clinic_api.py`/`pricing.py` in Kotlin; the Chaquopy
build stages the identical Python source tree into the APK (`android/aura-*/app/build.gradle`
`stageAuraPython` task; confirmed by the data-integrity sweep finding the *only*
occurrences of `Room`/`SQLiteOpenHelper`/native DB code under `android/` are the
staged copies of these exact `.py` files). So **any given HTTP request that reaches
the server is processed identically regardless of platform** — the only place
platform divergence can occur is in what the **client** computes and sends before
that request is made. This is why the Retail finding in `03` is entirely a
client-side (Kotlin UI) defect, not a backend fork.

## Retail — synthetic input comparison

Input: one product, `sell_price=100`, `tax_rate=15`, `discount_pct=10`, qty=1,
company `tax_calculation_mode` = default (`after_discount`).

| Field | Windows/web POS (`subsystem-retail.js` → `create_sale`) | Android POS (`RetailScreens.kt` → `create_sale`) | Match? |
|---|---|---|---|
| subtotal | 100.00 (computed via `_recalc()`, matches `pricing.calculate_line`) | 100.00 (raw `sell_price * qty`, discount not modeled but happens to equal gross here since Android has no discount concept) | Coincidentally equal for this field only |
| discount_amount | 10.00 | **0.00** (no discount UI exists) | **NO** |
| tax | 13.50 (`(100-10)*0.15`) | **0.00** (never computed) | **NO** |
| total | 103.50 | **100.00** | **NO — Android undercharges by 3.50 on this single line, and by more as tax_rate or cart size grows** |
| `sale_items.tax_rate` (persisted) | 15 | **0** (field doesn't exist in Android's `SaleItemReq`, defaults to server's `item.get('tax_rate',0)`) | **NO** |
| `sale_items.discount_pct` (persisted) | 10 | **0** | **NO** |

**Every financial field differs between platforms for any cart involving tax or
discount.** For a tax-free, discount-free cart, the two platforms happen to agree
(both just sum `unit_price * qty`), which is exactly why this was never caught by
manual testing that didn't specifically try a taxable/discounted item on Android.

## Retail — refund/return comparison

Both platforms hit the same `create_return()` route, which (per `03`) applies
**no validation on either platform** — the return-abuse finding (unlimited
repeated returns, no original-sale linkage) is **platform-independent**: it is
exactly as exploitable from Windows as from Android, since it is a server-side
gap, not a client computation. Windows currently doesn't expose obviously more
return-abuse surface than Android in the UI, but neither platform is protected by
the server either way.

## Retail — inventory quantity comparison

Both platforms decrement `inventory_balances` identically via the same server
code (`create_sale` lines 665-668) — no platform divergence here, since inventory
deduction is server-side and not client-computed. **Consistent.**

## Clinic — synthetic input comparison

Clinic's `create_invoice()`/`record_payment()` compute `subtotal`/`discount`/`tax`/`total`
**entirely server-side** from `items`/`discount`/`tax_rate` fields the client
submits, with clamping (`03`... i.e. `04`). Whether the client is the Windows/web
frontend or the Android app, both submit the same shape of request
(`patient_id`, `items[]`, `discount`, `tax_rate`) and the server computes the
same result. **This makes Clinic's financial output structurally guaranteed to be
platform-consistent by construction** — there is no client-side arithmetic to
drift, unlike Retail. This was not independently re-verified by placing an actual
side-by-side Android UI trace in this pass (no device), but the guarantee follows
directly from the server owning 100% of the computation — a UNVERIFIED-by-device,
PROVEN-by-architecture distinction worth being explicit about.

## Verdict

| | Retail | Clinic |
|---|---|---|
| Cross-platform financial consistency | **PROVEN BROKEN** — Android omits tax and discount on every sale | **PROVEN CONSISTENT BY CONSTRUCTION** (server-side computation on both platforms) |
| Root cause | Retail's financial architecture puts computation in each client (web JS does it correctly, Android was never given the same logic) | Clinic's financial architecture puts computation in the server (no client can get it wrong) |
| Recommended direction (not implemented — analysis only) | Retail should adopt Clinic's pattern: move `subtotal`/`discount`/`tax`/`total` computation into `create_sale()` itself using `core/retail/pricing.py` (which already exists and is already correct — it's just not called), and reduce the client's job to submitting line items + a chosen discount, not final totals. This would fix the Android gap and close the "server trusts client money" gap in the same change. | No corrective action indicated by this finding — Clinic's payment validation gap (`04`) is a separate, unrelated issue. |

This is the single clearest piece of evidence in the whole audit for "is either
product enterprise-grade": a company running Retail on a mix of Windows tills and
Android tablets is **currently under-collecting sales tax and unable to apply
discounts on every Android transaction**, silently, with no error, warning, or log
entry anywhere in the pipeline.
