# Aura Retail Unified Mobile — Milestone 7 Test Report

Real, executed evidence only.

## Test count progression

| Checkpoint | Shared tests | Delta |
|---|---|---|
| M6 close (accepted, CONDITIONAL PASS) | 539 | — |
| M6 follow-up (`MainActivityWiringRegressionTest`, required by the M7 checkpoint before M7 work began) | 541 | +2 |
| After M7.17/M7.18 (`LicensingContractTest`, 23 tests) | **564** | +23 |

**Net M7 contribution: 25 new tests (539 → 564), 0 failures, 0 errors**
(`shared/build/test-results/testDebugUnitTest/*.xml`, summed across
all 82 result files: `total_tests=564 failures=0 errors=0`).

## New test files this milestone

| File | Tests | Covers |
|---|---|---|
| `MainActivityWiringRegressionTest.kt` | 2 | M6 follow-up requirement — real MainActivity/AuraNavHost source-level regression coverage for the 4 named M6 defects |
| `LicensingContractTest.kt` | 23 | M7.17/M7.18 — shared licensing contract model shape, serialization round-trip, data-minimization field-set proof, error-contract parsing, 11 fixture scenarios |

## Cross-language and platform checks

- Retail Python: not re-run this milestone (no Python file was
  touched — M7 is Owner-audit-and-docs plus Kotlin-only contract
  models; the Owner codebase itself was read-only inspected, never
  modified, per the governing checkpoint's own explicit prohibition).
- Android debug APK: built successfully after the M7.17/M7.18 commit
  (`:androidApp:assembleDebug`, `BUILD SUCCESSFUL`).
- iOS: not claimed, per standing constraint (unchanged since M6).
- Real on-device Android execution: not performed (unchanged since
  M6 — no adb/emulator on this host).
- Owner backend: read-only inspected via Explore-agent research
  (foreground, citation-required) across `owner/app/`, `owner/
  migrations/`, `owner/tests/`, and `commercial_runtime/
  licensing_contracts/`. Zero write operations performed against
  `owner/` at any point in this milestone.

## Real findings during this milestone (not merely "no bugs found")

1. **Four independent, unreconciled device-limit representations**
   found in Owner's own schema (`Plan.max_device_count`,
   `Subscription.device_allowance`, generic `max_devices`
   `PlanEntitlement`, and the actually-enforced `License.device_limit`)
   — documented, not fixed (`OWNER_SERVER` gap,
   `licensing-gap-ownership-matrix.md`).
2. **No customer-facing authentication exists anywhere in Owner** —
   confirmed absent via six independent lines of negative evidence,
   not merely "not found" (`customer-authentication-gap-analysis.md`).
3. **`commercial_runtime/licensing_contracts/canonical.py`'s own
   module docstring references a shared conformance fixture file
   (`tests/fixtures/canonical_vectors.json`) that does not exist** —
   a real, previously-undocumented drift-risk gap in that package,
   worked around (not silently ignored) by building an independent
   mobile-side fixture set for M7.18
   (`offline-license-lease-contract-audit.md`).
4. **`Installation.device_public_key` (a real schema column) is
   confirmed dead code** — never read or written by any real
   activation/check-in/deactivation code path, despite being a
   real, present column (`installation-authority-contract.md`).
5. **A real, unresolved, honestly-disclosed drift in the legacy
   `AuraEnterprise` workspace's own pre-existing untracked-file
   count** (19 → 15) was found during M7 exit fingerprinting — tracked
   file content proven byte-identical (strong evidence no edit
   occurred), but the untracked-file discrepancy itself could not be
   explained by anything this session did, and is surfaced rather than
   silently reconciled (`external-workspace-exit-fingerprints-m7.md`).

## Real, deliberately unfixed / out-of-scope items

Every item in `licensing-gap-ownership-matrix.md` — 12 real gaps,
classified by ownership, none touched by M7 per its own explicit scope
restrictions (no server-side fix, no activation implementation, no
iOS platform work, no secure storage, no offline verification wiring).
