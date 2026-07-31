# Phase 8V-P9 — Retail Discount Contract Decision

## Decision: Branch B — Discount is not a current Retail Android capability. No UI implemented.

## Canonical evidence

See `canonical-discount-and-export-contract.md` Q1/Q2 for full citations. Summary: three independent
real audit generations (`docs/audit/19-retail-windows-vs-android-parity.md`,
`docs/android/phase4/retail-android-parity-matrix.md`,
`docs/android/phase4/android-residual-risk-register.md`) all classify Android discount-entry as a real,
known, intentionally-absent, non-regression feature -- never a broken promise. Source comments in
`Models.kt` and `RetailScreens.kt` state this directly.

## Requirements satisfied for Branch B

1. Exact canonical evidence cited above (file:line, direct quotes, in
   `canonical-discount-and-export-contract.md`).
2. Backend 88.00 calculation still passes automated regression: confirmed in this session's Part P
   regression run (`retail_financial_authority_test.py::test_worked_example_subtotal_100_discount_20pct_tax_10pct`
   included, passing).
3. No other supported client path is documented as "owning" Android discount entry -- Windows Retail
   also has no discount-entry UI in its own frontend (not investigated further this session since it's
   out of the Android-specific question this branch answers; not claimed either way).
4. The incorrectly-introduced "physical Android 88.00 release gate" from prior sessions' own governing
   specs is removed as a blocking gate this session -- those specs treated an unwritten UI feature as
   though it were a regression, which the real evidence above does not support.
5. Backend 88.00 test retained as mandatory (already is, part of the standard regression suite).
6. No feature-matrix update needed this session -- `docs/android/phase4/retail-android-parity-matrix.md`
   and `docs/android/phase4/android-residual-risk-register.md` already correctly document Android as
   not supporting discount entry; no document anywhere overclaims it.
7. Classification: **product-parity backlog item** (a real, known gap worth building eventually, not
   promised for any past or current phase).
8. No physical Android 88.00 UI PASS is reported.
9. Reported plainly: **NOT APPLICABLE TO CURRENT ANDROID PRODUCT CONTRACT.**

## What this means for the final Phase 8 decision

Per this session's own governing spec: "Retail 88.00 PASS where Android discount is required, or
canonically proven not applicable to current Android scope." The second condition is met, with real,
thorough, multi-source evidence -- this item is resolved, not left open.
