# Android Secure Storage Runtime Report (M10.26)

Real, honest disclosure of what M10's Android secure-storage work has
and has not been verified against, on this Windows development host.

## What is real and verified here

- **Compilation**: `AndroidSecureBlobStore.kt` compiles against the
  real Android target — `:shared:compileDebugKotlinAndroid`, `BUILD
  SUCCESSFUL` (`android-secure-storage-implementation-report.md`,
  M10.8).
- **Atomicity/generation/recovery logic**: real, executed, passing —
  all of this logic lives in `GenerationalSecureMaterialStore`
  (`commonMain`), exercised by `SecureMaterialStoreTest` (14 tests)
  and `SecureMaterialStoreActivationSinkTest` (3 tests) against the
  real, deterministic `InMemorySecureBlobStore` fault-injecting test
  double — these tests run on the JVM (`:shared:testDebugUnitTest`)
  and are real, host-executable, not simulated Android behavior.
- **CSPRNG regression coverage**: real, executed —
  `SecureRandomRegressionTest` (M10.25) runs under
  `:shared:testDebugUnitTest`, JVM-hosted, confirms the real Android
  actual (`SecureRandomBytes.android.kt`) source never falls back to
  `kotlin.random.Random`.
- **Debug APK build**: the shared module and Android app target build
  successfully on this host (unchanged since M6/M7 baseline;
  re-confirmed still green through M10's own commits).

## What is NOT verified — real, honest limitation

**No Android device or emulator exists on this Windows host.** The
following real, written Android-specific code has never executed on
a real or emulated Android runtime, and this milestone does not claim
otherwise:

- `getOrCreateWrappingKey`/`buildWrappingKeySpec` — real
  `AndroidKeyStore` key generation, including the real
  StrongBox-attempted-with-fallback path (`catches Throwable`,
  mirroring `DeviceIdentity.kt`'s own physically-tested Phase 7V-A
  design). The fallback branch itself has never been exercised on a
  device without StrongBox, nor has the StrongBox-present branch been
  exercised on a device with it.
- `Cipher`-based AES-GCM encrypt/decrypt against the real Android JCE
  provider (`AndroidKeyStore` `Cipher.getInstance("AES/GCM/NoPadding")`)
  — the in-memory test double (`InMemorySecureBlobStore`) reproduces
  the same AEAD-failure *contract* (AD mismatch fails closed,
  corruption fails closed) but is not the real JCE provider and cannot
  substitute for running the real cipher path.
- `writeAtomic`'s real temp-file-then-rename behavior against a real
  Android filesystem (`File.renameTo`) — the atomicity *logic* is
  proven at the `GenerationalSecureMaterialStore` layer, but the real
  filesystem call itself is unverified here.
- Real `AEADBadTagException`/missing-alias behavior on a real device
  across OS-level Keystore resets, app reinstalls, or StrongBox
  hardware faults.
- Real on-device performance/latency of Keystore-backed encryption
  under the concurrency patterns `secure-storage-performance-
  concurrency.md` (M10.30) describes.

## Honest classification

**Android secure storage: CONDITIONAL — compile-verified and
architecture-verified (via the real, executed common-layer test
suite), runtime-NOT-VERIFIED.** This matches the M10 checkpoint's own
stated expectation for this host: "the expected maximum honest
verdict is normally: CONDITIONAL PASS unless real Android and real
macOS/iOS validation are provided from separate authorized
environments." No fake or simulated device evidence is presented
anywhere in this milestone's documentation or test reports.

## What would close this gap

Running the existing, real, unmodified Android instrumented-test
target (`AndroidSecureBlobStore` exercised directly, not through the
JVM unit-test double) on a real device or emulator with Android
Keystore support, covering: key generation (StrongBox present and
absent), encrypt/decrypt round-trip, AAD-mismatch rejection,
corruption rejection, key rotation, and `writeAtomic` under a
simulated process kill between temp-write and rename. None of this is
executed in this milestone; this section records the real, specific
gap, not a promise it was closed.
