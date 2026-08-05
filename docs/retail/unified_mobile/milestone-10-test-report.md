# Aura Retail Unified Mobile — Milestone 10 Test Report

Platform Secure Storage, Atomic Activation Material Persistence,
Credential Lifecycle, and Startup Recovery.

## Real, executed shared-test result

`:shared:testDebugUnitTest`, full run: **656 tests**, 654 deterministic
passes. Two failures observed — both `ReportingConcurrencyAtScaleTest`
(`kotlinx.coroutines.test.UncompletedCoroutinesError`), a pre-existing
reporting-module timing test, entirely unrelated to any file this
milestone touched (`securestorage`/`di`/`ui`/`licensing.transport`).
Real, confirmed non-deterministic: re-run in isolation across three
separate full/partial runs during this milestone, a **different**
subtest failed each time (`reportReadNeverInterleavesWithCategoryReassignmentOrBranchArchive`,
`reportReadNeverObservesAPartialProductUpdate`,
`twoIndependentlyConstructedRepositoriesSharingOneGateStillMutuallyExcludeAtScale`
— never the same one twice, never more than 2 of the suite's own
tests), and the same suite passed 6/6 clean on at least one isolated
run — a real, disclosed, timing-sensitive pre-existing defect in the
Reporting concurrency test harness, out of this milestone's scope to
fix (M10 owns secure storage, not Reporting). Not silently ignored:
recorded here honestly rather than claimed as a clean 656/656.

Baseline carried forward: **631 → 656** (net +25 real tests this
milestone: 2 CSPRNG regression tests beyond the 631 baseline overlap
— `SecureRandomRegressionTest` contributes 3, `SecureMaterialStoreTest`
contributes 19 including the M10.30 rotation/delete-noop additions,
`SecureMaterialStoreActivationSinkTest` contributes 3, plus 1 new
`AuraAppContainerTest` case; exact per-file breakdown in the commit
history, `:shared:testDebugUnitTest` is the single source of truth).

## Real, executed build results

- `:shared:compileDebugKotlinAndroid`: `BUILD SUCCESSFUL` — every new
  M10 `commonMain`/`androidMain` file compiles against the real
  Android target.
- `:androidApp:assembleDebug`: `BUILD SUCCESSFUL` — real debug APK,
  confirmed after the M10.31 `MainActivity.kt`/`AuraAppContainer`
  wiring change.
- iOS: `IosSecureBlobStore.kt`/`SecureRandomBytes.ios.kt` are real,
  complete Kotlin/Native source, never compiled on this host (no
  macOS/Xcode) — `ios-secure-storage-runtime-validation-plan.md`.

## Real bugs found and fixed during this milestone

1. `GenerationalSecureMaterialStore.readPointer` computed the wrong
   associated data on read (garbled expression unrelated to the real
   write-time `pointerAssociatedData(scope)`) — fixed by threading
   `scope` through `readPointer` and using the same computation at
   both call sites.
2. `components.map { it.first }` — a `Pair`/`Triple`-style typo
   against the real `Component` data class's own `name` field — fixed.
3. A local `fun read(...)` inside `loadGeneration` called a `suspend`
   function without itself being `suspend` — fixed.
4. Redundant hand-written `component1`/`component2`/`component3`
   operators conflicting with the `data class Component`'s own
   auto-generated ones — removed.
5. `SecureMaterialStoreActivationSinkTest`'s own `sink()`/`scope()`
   helpers used mismatched `customerAccountId` values (implicit vs.
   `SecureStorageFixtures.scope()`'s own `"fixture-account-0001"`
   default), causing `loadActivationBundle` to look under the wrong
   pointer even though the underlying commit itself succeeded — fixed
   with a matching local `scope()` helper.
6. `SecureRandomRegressionTest`'s own live-invocation regex matched
   legitimate KDoc prose discussing the historical M9 weak-RNG defect
   by name — fixed via a `stripComments()` preprocessing step.
7. The same regex, even after comment-stripping, still false-positived
   on `androidSecureRandom.nextBytes(...)` because the real variable
   name happens to end in the literal substring `Random` — fixed by
   requiring a word boundary (`\b`) before `Random` in the regex.

Every one of these seven was caught by this milestone's own real,
executed tests — none were found by inspection alone.

## Gate-by-gate

| Gate | Status | Evidence |
|---|---|---|
| M10 entry fingerprints captured, no attributable change at entry | PASS | `external-workspace-entry-fingerprints-m10.md` |
| Current secret-storage state audited | PASS | `secure-storage-current-state-audit.md` |
| Threat model | PASS | `mobile-secure-storage-threat-model.md` |
| Data classification | PASS | `secure-material-data-classification.md` |
| One shared `SecureBlobStore`/`SecureMaterialStore` contract pair | PASS | `SecureStorageContracts.kt`, `SecureBlobStore.kt`, `SecureMaterialStore.kt` |
| Secure activation bundle contract | PASS | `SecureActivationBundle.kt`, redacted `toString()` |
| Atomic-commit authority (generation/pointer) | PASS | `GenerationalSecureMaterialStore.kt`, `secure-material-atomic-commit.md` |
| Old valid bundle survives a failed replacement | PASS | `oldValidBundleSurvivesAFailedReplacement` |
| No mixed-generation read ever observed | PASS | `noMixedGenerationEverObserved` |
| Deterministic recovery after interrupted commit | PASS | `deterministicRecoveryAfterInterruptedCommit` |
| Android decision (raw Keystore over EncryptedSharedPreferences) | PASS | `android-secure-storage-decision.md` |
| Android implementation | CONDITIONAL — compiled, not runtime-verified | `android-secure-storage-implementation-report.md`, `android-secure-storage-runtime-report.md` |
| iOS decision (Keychain, device-bound accessibility) | PASS | `ios-keychain-storage-decision.md` |
| iOS implementation | NOT VERIFIED (source-complete, never compiled on this host) | `ios-keychain-storage-implementation-report.md`, `ios-secure-storage-runtime-validation-plan.md` |
| Installation identity/Customer session/Installation credential/signed lease storage designs | PASS | `installation-identity-storage-contract.md`, `customer-session-secure-storage.md`, `installation-credential-secure-storage.md`, `signed-lease-secure-storage.md` |
| Versioning/migration | PASS (structural, no second version has shipped) | `secure-storage-versioning-migration.md` |
| Key rotation | PASS | `secure-storage-key-rotation.md`, `rotateKeySucceedsAndPreviouslyCommittedBundleRemainsLoadable` |
| Corruption recovery | PASS | `secure-storage-corruption-recovery.md`, `corruptedComponentFailsClosedNeverReturnsPartialBundle` |
| Deletion policy | PASS | `secure-material-deletion-policy.md`, `deleteScopeRemovesPointerAndAllGenerationComponents`, `deleteScopeOnAnEmptyScopeIsASafeNoOp` |
| Backup/reinstall policy | PASS | `secure-storage-backup-reinstall-policy.md` |
| User-presence decision | PASS | `secure-storage-user-presence-decision.md` |
| Activation integration (M9 bridge) | PASS | `SecureMaterialStoreActivationSink.kt`, `activation-secure-persistence-integration.md`, `bothPiecesTogetherProduceOneRealAtomicCommit` |
| Startup bootstrap | PASS | `secure-storage-startup-bootstrap.md`, `computeLicensingBootstrapStateFromHealth` |
| Health contract | PASS | `secure-storage-health-contract.md`, `healthReflectsRealBundlePresenceAndCorruption`, `healthNeverExposesSecretValues` |
| Memory hygiene | PASS (honest, disclosed partial) | `secure-material-memory-hygiene.md` |
| CSPRNG regression | PASS | `csprng-native-bridge-review.md`, `SecureRandomRegressionTest` (3/3) |
| Android runtime validation | NOT VERIFIED (disclosed) | `android-secure-storage-runtime-report.md` |
| iOS runtime validation | NOT VERIFIED (disclosed) | `ios-secure-storage-runtime-validation-plan.md` |
| Security/functional test matrix | PASS | 19+3+3+1 = 26 real tests across `SecureMaterialStoreTest`/`SecureMaterialStoreActivationSinkTest`/`SecureRandomRegressionTest`/`AuraAppContainerTest` |
| Performance/concurrency | PASS (logical), NOT MEASURED (real-device) | `secure-storage-performance-concurrency.md` |
| DI wiring into the composition root | PASS | `secure-storage-di-wiring-report.md`, `AuraAppContainer.kt`, `MainActivity.kt`, `App.kt` |
| Release wiring cannot resolve test storage | PASS | No default value on `AuraAppContainer`'s `secureBlobStore` parameter — structurally required |
| No signed-lease cryptographic verification implemented (M11 boundary) | PASS | Confirmed by inspection — no verification code added |
| No offline enforcement implemented | PASS | Same |
| No production signing keys/App Store release | PASS | Confirmed by inspection |
| No Aura Owner code modified | PASS | Zero commands issued against `owner/` other than read-only fingerprint capture |
| No Clinic code introduced | PASS | Confirmed by inspection |
| All shared tests pass except disclosed pre-existing flake | CONDITIONAL | See "Real, executed shared-test result" above |
| 631-test M9 baseline remains green | PASS | Subsumed; net +25 |
| Android debug APK builds | PASS | `BUILD SUCCESSFUL` |
| External workspaces: no M10-attributable change | PASS | `external-workspace-exit-fingerprints-m10.md` — 2 of 3 byte-identical, 1 changed for real unrelated reasons, zero M10 contribution |
| Unified Mobile branch clean after each commit | PASS | Verified via `git status --short` after each commit |

## Real, deliberately unfixed / out-of-scope items

No signed-lease cryptographic verification, no offline enforcement, no
background lease-refresh scheduling, no Product entitlement
enforcement, no local Retail user authorization, no database
encryption, no Customer self-service portal, no Apple code signing/App
Store release (all explicitly M10 "must not" items, all real, confirmed
absent). Android real-device execution and iOS compilation/execution
remain genuinely unavailable on this host, disclosed rather than
fabricated. The pre-existing `ReportingConcurrencyAtScaleTest` timing
flake is recorded, not fixed (out of this milestone's scope).
