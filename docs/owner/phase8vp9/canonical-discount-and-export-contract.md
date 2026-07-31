# Phase 8V-P9 — Canonical Discount and Export Contract Audit

Research method: broad source search across `docs/audit/`, `docs/android/phase4/`,
`docs/architecture/financial-authority-contracts.md`, `docs/migration/`, `docs/release/`,
`android/aura-retail` Kotlin source, and both backends. Every claim below is a direct file:line
citation, not inference from a prior validation session's own prompt text.

## Q1: Retail Android discount-entry UI

**Classification: NOT IN CURRENT PRODUCT CONTRACT.**

- `docs/audit/19-retail-windows-vs-android-parity.md:12` (pre-Wave-0, before server became financial
  authority): flagged as a release blocker *at that time*, because Android's total silently omitted
  discount/tax entirely (a real bug, since fixed).
- `docs/architecture/financial-authority-contracts.md` (Wave 0 fix, commit `53911a9`): server became
  the sole financial authority; `items[].discount_pct` became an optional, client-suppliable,
  server-clamped field -- but the document assigns **zero UI responsibility to any client** (confirmed
  by full read, see below).
- `docs/android/phase4/retail-android-parity-matrix.md:21` (the corrective re-assessment, written
  *after* Wave 0): "Discount | PASS WITH DOCUMENTED LIMITATION | Client can only ever send
  `discount_pct=0` (no discount-entry UI in source) -- clamped/validated server-side regardless;
  **NOT a regression, just an absent feature**."
- `docs/android/phase4/android-residual-risk-register.md:43-48`: "This was true before this phase too
  (not a regression); the field exists on the wire contract specifically so a future UI addition
  doesn't require another backend/model change."
- Source: `android/aura-retail/.../net/Models.kt:181-183` and `.../ui/screens/RetailScreens.kt:383-384`
  both state directly, in comments, that no discount-entry UI exists in this source.
- Reconfirmed as still true at the very end of this project's real physical-device history:
  `docs/owner/phase8vp7/retail-88-and-return-proof.md:5-6`.

No commit, doc, or requirement anywhere promises Android discount entry. Every real audit generation
across the project's history classifies this the same way: a real, known, intentionally-absent
feature, not a broken promise or regression.

## Q2: 88.00 as an Android UI-level requirement

**Classification: SUPPORTED BACKEND ONLY.**

The exact case (`subtotal=100, discount_pct=20%, tax=10% -> total=88.00`) originates in
`products/retail/tests/retail_financial_authority_test.py::test_worked_example_subtotal_100_discount_20pct_tax_10pct`
-- a backend pytest that POSTs directly to `/api/sub/retail/sales`. It is documented as the canonical
worked example in `docs/architecture/financial-authority-contracts.md:63-65`, tied explicitly to that
backend test. The only Android-side artifact referencing this case is
`android/aura-retail/.../net/SaleContractTest.kt::saleResult_deserializes_the_full_authoritative_contract`
-- a JVM unit test that deserializes a *fixture JSON string simulating a server response*; it never
drives any UI screen, and a second Android test constructs the request object directly in code,
bypassing the UI entirely. No Android instrumentation/UI test anywhere exercises this case through
real screens. Confirmed unreachable through the real UI on the real physical device this session
(source-verified: no discount-entry field exists to reach it).

## Q3/Q4: Export (Retail Android, Clinic Android)

**Classification: NOT IN CURRENT PRODUCT CONTRACT, either product, either platform.**

- Windows Retail: `products/retail/backend/api/import_api.py:31-34` (source comment): "No export
  endpoint exists in the source implementation ... This is not a regression introduced by
  extraction." `docs/migration/retail-parity-matrix.md:22` confirms with its own marker test,
  `test_no_export_endpoint_exists_yet`.
- Backend-wide: `docs/audit/18-backup-and-recovery-audit.md:39-41`: "Retail has no export capability
  at all ... Clinic was not found to have one either."
- `docs/audit/19-retail-windows-vs-android-parity.md:18`: "Export | NOT PRESENT (both) | N/A (never
  existed in source)."
- A `retail.data.export`/`clinic.data.export` string exists only as an entry in each backend's
  `READ_ONLY_ALLOWLIST` (a licensing-restriction capability-code set) -- confirmed by grep that **no
  route or handler implements it anywhere in the repo**.
- The Licensing screen's own copy ("backup, restore, and export remain available") is disclaimer
  text, not a real feature -- `docs/release/wave1c/release-gate-scorecard.md:77` independently flags
  this: "Compliance-readiness controls | FAIL -- ... no export capability."
- Final, most-recent physical confirmation: `docs/owner/phase8vp7/android-backup-restore-export-final.md:29-36`
  -- no Export UI located anywhere on either product this session either.

## What "recovery access" canonically means

**Backup + Restore only.** The real, implemented, and now-physically-proven (PASS, both products,
Phase 8V-P7) recovery contract is the `.aurabak.zip` workflow under
`Settings -> Backup & restore -> Create backup / Restore backup`. "Export" is a permission-string
placeholder (`READ_ONLY_ALLOWLIST` entry) and UI-copy artifact only -- never an implemented capability
on any platform, in any product, at any point in this project's real history.

## Whether Export is deferred to a later phase

Yes, implicitly -- every document that mentions its absence treats it as a real, known gap ("marker
for future work," per `docs/migration/retail-parity-matrix.md`) rather than a Phase 8 licensing-closure
requirement. Phase 8's own scope is commercial licensing enforcement, not data-portability features;
Export was never part of any Phase 8 spec's own "STRUCTURAL FIXES ALREADY COMPLETED" section either.

## Outcome for this session

Both `retail-discount-contract-decision.md` and `export-contract-decision.md` select **Branch B** (no
new UI implementation) on this real, thorough, multi-source evidence -- not because the work is
inconvenient, but because the product never promised either capability on Android, and the governing
spec's own instruction is explicit: "Do not add an unrelated feature only because an earlier
validation prompt over-specified the scope."
