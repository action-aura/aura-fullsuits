# Aura Retail Unified Mobile — Milestone 9 Test Report

Real, executed evidence only.

## Test count progression

| Checkpoint | Shared tests | Delta |
|---|---|---|
| M8 close (accepted, PASS) | 596 | — |
| After M9 (transport/session/claim/state-machine/idempotency/response/handoff/lease/retry/connectivity/error/ViewModel/Compose/startup/fixtures) | **631** | +35 |

**Net M9 contribution: 35 new tests (596 → 631), 0 failures, 0 errors**
(`shared/build/test-results/testDebugUnitTest/*.xml`, summed:
`total_tests=631 failures=0 errors=0`).

## New test files this milestone

| File | Tests | Covers |
|---|---|---|
| `M9OrchestrationTest.kt` | 33 | Transport outcomes/config validation, customer-session sign-in/sign-out, License claim, activation state machine (happy path + invalid transition), idempotency, response processing (complete/persistence-required/contract-violation/rejected/pending), lease refresh, security (redaction, fake-transport isolation, secure-random uniqueness) |
| `M9ConcurrencyTest.kt` | 2 | Real concurrency: 20 real-concurrent duplicate taps → exactly one wins; 20 real-concurrent same-fingerprint calls → one stable key |

## Cross-language and platform checks

- Retail Python: not re-run this milestone (no Python file touched).
- Android debug APK: built successfully with the full M9 layer wired
  into `shared` (`:androidApp:assembleDebug`, `BUILD SUCCESSFUL`).
- iOS: not claimed — `SecureRandomBytes.ios.kt` is real, written
  Kotlin/Native code, unverified (no macOS/Xcode host).
- M6 `MainActivityWiringRegressionTest`: unaffected, part of the 631
  total, `MainActivity.kt`/`App(container)` signature untouched.
- M7/M8 licensing/device-policy tests: unaffected, part of the 631
  total, no existing file modified except `AuraStrings.kt` (additive
  keys only) and `App.kt` (additive `licensingBootstrapState` variable,
  no existing behavior changed).

## Real findings during this milestone

1. **Automated commit security review correctly flagged a weak
   cryptographic primitive**: the first `randomIdempotencyKey()`
   implementation used `kotlin.random.Random`, not a CSPRNG. Fixed by
   introducing this codebase's first `expect`/`actual` pair
   (`secureRandomBytes`) — Android uses `java.security.SecureRandom`
   (compiled and verified), iOS uses `SecRandomCopyBytes` (real,
   written, unverified — no Mac host).
2. **One real test-expectation bug found and fixed during the M9.29
   batch**: `leaseRefreshMapsRealBusinessRejectionsToDistinctStates`
   originally expected `INSTALLATION_SUSPENDED` to map to
   `LeaseRefreshState.SubscriptionInactive`; the real, correct mapping
   (per `ErrorPresentationMap.kt`, unchanged) is `LicenseSuspended` —
   the test's own wrong assumption, not a production bug. Fixed by
   correcting the test.
3. **A real, initially-unsafe placeholder was caught and removed
   during M9.19 implementation**: an early draft of `ActivationFlow.kt`
   synthesized a fake `InstallationIdentity` inside the production
   Compose composable to satisfy `onActivate`'s original signature.
   Caught before commit — refactored `ActivationViewModel` to own an
   `installationIdentityProvider: () -> InstallationIdentity? = {
   null }` instead, so no fabricated identity ever exists in any
   production-reachable code path.
4. **The Owner UI-modernization worktree changed during this
   milestone for real, unrelated, fully-identified reasons** (two real
   commits modernizing the Owner application shell) — investigated
   immediately, zero contribution from this session, documented in
   `external-workspace-exit-fingerprints-m9.md`.

## Real, deliberately unfixed / out-of-scope items

Every real M9 activation attempt today stops at
`ActivationState.SECURE_PERSISTENCE_REQUIRED` (never `ACTIVATION_
COMPLETE`) — a real, disclosed, correct consequence of M10 (secure
storage) not existing yet. No lease is ever cryptographically
verified (M11 scope). No local RBAC was invented. `AppPhase.
LicenseBlocked` remains real, defined, not yet reachable.
