# Aura Retail Unified Mobile — Milestone 8 Test Report

Real, executed evidence only.

## Test count progression

| Checkpoint | Shared tests | Delta |
|---|---|---|
| M7 close (accepted, CONDITIONAL PASS) | 564 | — |
| After M8 (Platform/DevicePolicy/Identity/Replacement/Metadata/iOS-readiness/Presentation contracts + fixtures) | **596** | +32 |

**Net M8 contribution: 32 new tests (564 → 596), 0 failures, 0 errors**
(`shared/build/test-results/testDebugUnitTest/*.xml`, summed:
`total_tests=596 failures=0 errors=0`).

## New/changed test files this milestone

| File | Tests | Covers |
|---|---|---|
| `LicensingContractTest.kt` (extended) | 25 (was 23; 1 renamed, 2 added) | M8.0 — `LicensingPlatform.IOS` addition, `PlatformDecodeResult` parse/reject behavior |
| `DevicePolicyContractTest.kt` | 24 | M8.1-M8.13 — resolved device policy validation, replacement/installation-status distinctness, iOS readiness, presentation states, identity redaction, metadata allowlist |
| `CanonicalVectorFixtureTest.kt` | 3 | M8.11 — recovered canonicalization vector structural integrity |

## Cross-language and platform checks

- Retail Python: not re-run this milestone (no Python file touched —
  M8 is Owner-audit-extension/docs plus Kotlin-only contract models;
  Owner itself was never read or modified in M8, unlike M7 which did
  real read-only Owner research — M8's own Owner-facing content is
  entirely specification documents built from M7's already-gathered
  evidence).
- Android debug APK: built successfully
  (`androidApp-debug.apk`, `:androidApp:assembleDebug`).
- iOS: not claimed, per standing constraint (unchanged since M6/M7) —
  `LicensingPlatform.IOS`'s existence as a client-side contract case
  is explicitly not a build/runtime claim (`ios-platform-readiness-
  state.md`).
- M6 real-container startup regression (`MainActivityWiringRegressionTest`):
  unaffected, still part of the 596-test total, not independently
  re-verified beyond the full-suite run (no `MainActivity`/`AuraNavHost`
  file was touched in M8).
- Owner backend: not read or modified in M8 — every M8.9/M8.10 Owner
  specification is built entirely from M7's own already-gathered,
  cited evidence, not new Owner-code reading.

## Real findings during this milestone

1. **No existing platform representation anywhere in the codebase
   (Owner, `commercial_runtime`, or either legacy Android client) was
   ever a closed, IOS-inclusive type** — every real production value
   is a bare, unvalidated string (`platform-contract-reconciliation-
   m8.md`).
2. **The M7.13-disclosed missing `commercial_runtime` canonicalization
   fixture's schema was fully recoverable** from three independent,
   mutually cross-checked real implementations (Owner, `commercial_
   runtime`'s own test suite, both legacy Android Kotlin ports) — not
   lost, just never materialized into the promised standalone file
   (`missing-commercial-runtime-fixture-investigation.md`).
3. **The M7 legacy-workspace untracked-file anomaly was investigated
   to its real limit**: no prior fingerprint doc ever recorded the
   literal untracked path list (only a count and a hash), so the
   specific missing paths could not be identified — a real, honest
   limitation of the fingerprint protocol as executed through M7, now
   corrected going forward (M8's own fingerprint docs record the
   literal path list, not just a hash).
4. **No further drift occurred during M8** — the legacy repo's
   untracked-file set (15 entries) was stable, byte-identical, across
   the entire M8 window (`external-workspace-exit-fingerprints-m8.md`).

## Real, deliberately unfixed / out-of-scope items

Every item in `licensing-gap-ownership-matrix.md` (M7) remains open
and unmodified, plus the two new Owner-side specifications
(`OWNER-M8-IOS-MULTIDEVICE-CHANGE-SPEC.md`,
`OWNER-EXTERNAL-CUSTOMER-IDENTITY-AND-LICENSING-BOUNDARY-SPEC.md`) — both real, cited,
implementation-ready, and explicitly **not applied** in this branch.
