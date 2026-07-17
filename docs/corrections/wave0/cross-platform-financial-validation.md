# Wave 0 — Cross-Platform Financial Validation

Status: **PROVEN** for Retail sales (Windows-shaped vs. Android-shaped
request bodies against the same backend). Android client code itself was
not modified in this wave (see below) — this document validates the
*backend contract* both platforms share, not a live run of the Android app.

## Method

`core.retail.pricing` and `products/retail/backend/api/retail_api.py`'s
`create_sale()` are the single financial-calculation code path for every
client — Windows desktop UI, the future web UI, and Android's Retrofit
client all call the same `POST /api/sub/retail/sales` route. Because the
server now resolves `unit_price`/`tax_rate` from the product row and
computes `discount_amount`/`tax_amount`/`total` itself (AUDIT-002/003
correction), the *shape* of the client's request no longer affects the
result — only `product_id`, `quantity`, and `discount_pct` do.

## Comparison table

Fixture: product `sell_price=100`, `tax_rate=15`, `discount_pct=0`,
`quantity=1`, after-discount mode (default).

| Request shape | Client sends | Server-computed result |
|---|---|---|
| "Windows-style" (desktop UI, full breakdown submitted) | `{unit_price: 100, tax_rate: 15, subtotal: 100, discount_amount: 0, tax_amount: 15, total: 115, ...}` | `tax_amount: 15.0, total: 115.0` |
| "Android-style" (historical client bug: always omits/zeroes tax+discount) | `{discount_pct: 0, tax_rate: 0}` (no `unit_price`, no `total`) | `tax_amount: 15.0, total: 115.0` |
| Tampered (arbitrary client-claimed total) | `{unit_price: 1, tax_rate: 0, total: 1}` | `tax_amount: 15.0, total: 115.0` |

All three converge on the identical authoritative result, because all
three inputs beyond `product_id`/`quantity`/`discount_pct` are ignored.
Verified directly by
`products/retail/tests/retail_financial_authority_test.py::test_server_ignores_manipulated_totals_and_unit_price`
and `::test_android_style_zero_tax_payload_still_computes_real_tax`, both
run against the same fixture product in the same test file.

## Android client code

The Android client's historical bug (always submitting `tax_rate: 0,
discount_pct: 0` regardless of the product's real configuration, per
`docs/audit/05-cross-platform-financial-parity.md`) is now harmless from a
financial-correctness standpoint, because the server no longer trusts those
fields at all — the bug can no longer under-collect tax. Per the explicit
Wave 0 scope boundary ("do not begin Phase 4 Android migration... client
may be corrected minimally if source is available in aura-fullsuits,
without doing broader Android migration/UI work"), the Android Kotlin
source itself was **not modified** in this wave: the backend fix alone
neutralizes the defect's financial impact, and the Android app was already
fully migrated in Phase 4 (tag `android-migration-phase4-complete`) with 0
tests — reopening that surface for a client-side patch was judged
out-of-scope for a backend-focused corrective wave. This is a deliberate
choice, not an oversight: **the Android client still sends the same
zero-tax payload it always did; it is simply no longer trusted.**

## What was not validated

- No live Android emulator/device run was performed in this wave (no
  Android build or install step was exercised) — this validation is at the
  HTTP-contract level (server ignores client-submitted financial fields
  regardless of shape), not an end-to-end Android UI test.
- Clinic has no equivalent client-diversity concern in this wave — Clinic's
  payment contract (`amount` only) has no analogous "client computes tax"
  step for a second platform to diverge on.
