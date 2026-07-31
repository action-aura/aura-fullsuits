# Phase 8V-P9 — Baseline

## Entry gate

Real device confirmed: Infinix X6528, Android 13, API 33, arm64-v8a, `adb devices -l` -> `device`
status. First attempt this session returned empty (real, reported honestly, no work started); user
reconnected; second attempt succeeded. Real clock: `Fri Jul 31 13:03:31 +03 2026`.

## Git baseline

HEAD `c32f361`, tree clean, branch `master`. Conditional tag
`aura-owner-commercial-ops-phase8-conditional-complete` unchanged (dereferences to `f593bce7...`).
Final tag `aura-commercial-licensing-operations-phase8-complete` does not exist. Original
`AuraEnterprise` repo not opened this session.

## Canonical scope audit result (see `canonical-discount-and-export-contract.md` for full evidence)

Delegated a thorough, source-cited research pass across docs/audit, docs/android/phase4,
docs/architecture/financial-authority-contracts.md, docs/migration, docs/release, Kotlin source, and
backend source. Conclusive findings, both **Branch B** (no new UI required):

- Retail Android discount-entry UI: **NOT IN CURRENT PRODUCT CONTRACT** -- repeatedly, explicitly
  documented across three real audit generations (pre-Wave-0 parity audit, post-Wave-0 corrective
  parity matrix, residual-risk register) as an absent, accepted, non-regression feature. Never
  promised for Android in any phase.
- 88.00 as an Android UI-level requirement: **SUPPORTED BACKEND ONLY** -- the exact case originates in
  a backend pytest (`retail_financial_authority_test.py`), mirrored only by a Kotlin
  response-deserialization unit test, never a UI test.
- Retail Android Export: **NOT IN CURRENT PRODUCT CONTRACT** -- no export route/handler exists in
  either backend, on either platform; confirmed by source comments, migration parity docs, and the
  Wave 1C backup-and-recovery audit.
- Clinic Android Export: **NOT IN CURRENT PRODUCT CONTRACT** -- same status.
- The real, committed, already-physically-proven recovery contract for both products/platforms is
  **Backup + Restore only**.

This resolves this phase's two largest open items without new UI implementation -- see
`retail-discount-contract-decision.md` and `export-contract-decision.md` for the formal branch
decisions and evidence citations.

## Carried-forward automated baseline

Owner 398/398, commercial_runtime+licensing_contracts 233/233, Retail 194/194, Clinic 135/135 =
960/960; Retail+Clinic re-confirmed 329/329 after the rc.4 version bump. Re-run in full this session
per Part P (see `final-regression-report.md`) rather than merely assumed, since Part E/F fixes below
touch real source.

## Known state at session start

Zero P0, zero P1. One carried P2 (local-deactivation UX, unchanged). Two real, disclosed pepper/
precision items to resolve this session per Parts E/F.
