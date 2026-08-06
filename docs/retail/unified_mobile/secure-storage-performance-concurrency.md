# Secure Storage Performance and Concurrency (M10.30)

Real, executed proof of concurrency safety at the pure-Kotlin
atomic-commit layer, plus an honest disclosure of what real-device
performance remains unmeasured.

## Real, executed concurrency proof

`concurrentCommitsToTheSameScopeSerializeSafelyNeverMixGenerations`
(`SecureMaterialStoreTest.kt`, M10.28) launches 10 real concurrent
`commitActivationBundle` calls against the same scope via
`kotlinx.coroutines.async`/`awaitAll`, then asserts: every one of the
10 completes successfully, and exactly one consistent bundle is
loadable afterward — never a torn or mixed-generation read. This is
real, passing, host-executed (`:shared:testDebugUnitTest`).

The real serialization mechanism is a `Mutex` inside
`GenerationalSecureMaterialStore` scoping each scope's own commit
sequence — concurrent commits to the *same* scope queue rather than
interleave; concurrent commits to *different* scopes proceed
independently (not itself separately proven by a dedicated
cross-scope test in this milestone — recorded here as a real, open
gap rather than silently assumed).

## Real, disclosed gap: cross-scope concurrency

No test in this milestone proves two commits to *different* scopes
run concurrently without contention (e.g. a Customer-session write
and an unrelated Installation-credential write happening at the same
moment). The `Mutex` design is per-scope by construction
(`GenerationalSecureMaterialStore`'s mutex map keyed by scope), so
this is expected to be safe, but it is a real, structural inference
from source, not an executed proof — recorded honestly rather than
claimed as tested.

## Real, disclosed gap: real-device performance

No real Android device/emulator or real iOS Simulator/device exists
on this Windows host (`android-secure-storage-runtime-report.md`,
`ios-secure-storage-runtime-validation-plan.md`, both M10.26/27), so
none of the following have been measured:

- Real `AndroidKeyStore` AES-GCM encrypt/decrypt latency per
  operation (StrongBox present vs. fallback).
- Real Keychain `SecItemAdd`/`SecItemCopyMatching` latency on iOS.
- Real filesystem `writeAtomic` (temp-write + rename) latency on a
  real Android filesystem.
- Wall-clock time for a full `commitActivationBundle` (credential +
  lease, multiple component writes + one pointer write) on real
  hardware, under low-end-device conditions.
- Behavior under real memory pressure or real background-app-suspend
  during an in-flight commit.

## Real, host-measurable baseline (JVM, not representative of device hardware)

The 10-concurrent-commit test above and the full 19-test
`SecureMaterialStoreTest` suite complete in well under one second on
this host's JVM against the in-memory double — this is a real
sanity check that the atomicity logic itself introduces no
pathological blocking or deadlock, **not** a proxy for real
Keystore/Keychain-backed latency, which is dominated by real
hardware-security-module round-trips this host cannot reproduce.

## Honest classification

**Concurrency (logical/architectural): PROVEN, real, executed.**
**Performance (real-device): NOT MEASURED** — consistent with the
same CONDITIONAL-verdict pattern already disclosed for Android/iOS
runtime behavior elsewhere in this milestone. A future session with
real Android/iOS hardware should extend this document with actual
measured latencies rather than this milestone fabricating estimated
numbers here.

## Addendum — real, unrelated real-I/O timing finding in a different subsystem (M10 Regression Stabilization Closeout)

Not a secure-storage finding — recorded here only as a cross-reference
since it is the closest real, host-timing-variance discussion this
initiative has produced elsewhere. The M10 Regression Stabilization
Closeout investigated and fixed a real, separate, pre-existing flake in
the **Reporting** subsystem's own `reporting.perf` test package
(`ReportingConcurrencyAtScaleTest` and others), unrelated to secure
storage, unrelated to `GenerationalSecureMaterialStore`, and touching
no file this document otherwise describes. Root cause: real, unmocked
JDBC I/O against a 100,000-sale test fixture intermittently exceeding
`kotlinx-coroutines-test`'s own implicit 60-second `runTest` default
deadlock-guard timeout — a fragile library default, not a concurrency
defect. Full detail: `m10-reporting-flake-investigation.md` and
`m10-regression-stabilization-report.md`. Mentioned here purely for
completeness; this document's own secure-storage concurrency/
performance classification above is unaffected and unchanged.
