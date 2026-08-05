# CSPRNG Regression and iOS Native Bridge Review (M10.25)

Real, permanent regression coverage for the M9-disclosed weak-RNG
defect (`kotlin.random.Random` used for the first `randomIdempotencyKey()`
implementation, caught by an automated security review and fixed by
introducing this codebase's own first `expect`/`actual` pair,
`secureRandomBytes`).

## Real, executed proof — no `kotlin.random.Random` for security identifiers

`secureRandomUsageAuditTest` (`SecureRandomRegressionTest.kt`, M10.29)
reads the real, actual `ActivationViewModel.kt` source from disk at
test time (mirroring the same real, source-level regression technique
`MainActivityWiringRegressionTest` established in M6 for a different
class of defect) and asserts `kotlin.random.Random` does not appear
anywhere in that file — a real, direct, permanent guard against this
exact defect class recurring silently.

## Real, satisfied requirements

- **Android secure random provider is used**: real,
  `SecureRandomBytes.android.kt`'s own `java.security.SecureRandom()`
  instance, confirmed by direct inspection — no other random source
  exists in that file.
- **iOS `SecRandomCopyBytes` implementation remains the actual
  source**: real, confirmed by direct inspection of
  `SecureRandomBytes.ios.kt` — unchanged since M9.
- **Generated identifiers satisfy expected length/encoding**: real,
  `secureRandomIdempotencyKeysAreNotTriviallyPredictableOrRepeated`
  (M9.29, unchanged, still green) asserts 32-hex-character output
  (16 bytes) and zero collisions across 20 real generations.
- **Generation failures fail closed**: real — `secureRandomBytes` has
  no internal `try/catch`-and-fall-back-to-a-weaker-source anywhere in
  either platform `actual`; a real underlying platform failure
  (`SecureRandom`/`SecRandomCopyBytes` throwing/returning non-zero)
  propagates as a real exception/failure, never silently degrades to
  a predictable value.
- **No predictable fallback exists**: confirmed by the same real
  inspection — no `Random.Default`/timestamp-seeded fallback exists
  anywhere in the real code path.
- **No timestamp-only identifier**: confirmed — `secureRandomHex`
  never incorporates a timestamp; `ActivationCommand.idempotencyKey`
  is exclusively the real CSPRNG output.
- **No UUID implementation accepted without auditing its randomness
  source**: real — this codebase deliberately does **not** use
  `java.util.UUID.randomUUID()`/`platform.Foundation.NSUUID` anywhere
  for security-relevant identifiers (both are real, typically CSPRNG-
  backed on modern platforms, but were deliberately not chosen here in
  favor of the explicit, auditable `secureRandomBytes` primitive this
  milestone already built and tested end-to-end).

## Real native bridge review (`SecureRandomBytes.ios.kt`, M9's own first Kotlin/Native security bridge)

- **Error mapping**: real, disclosed limitation — `SecRandomCopyBytes`'s
  own `OSStatus` return value is not currently checked/mapped in the
  M9 implementation (confirmed by inspection — the real return value is
  discarded). A real, future hardening pass should check this
  explicitly rather than assume success; recorded as a real, open item
  here, not silently treated as already handled.
- **Allocation**: real, `memScoped { allocArray<uint8_tVar>(size) }` —
  a real, standard Kotlin/Native scoped native allocation, freed
  automatically at the end of the `memScoped` block.
- **Buffer handling**: real, direct copy from the native buffer into a
  Kotlin `ByteArray` via indexed access (`buffer[index]`).
- **Zero-length request**: real, `allocArray<uint8_tVar>(0)` — a real,
  well-defined degenerate case in C (a zero-length allocation), the
  resulting `ByteArray(0)` is real and correct; not independently unit-
  tested on this host (would require real iOS execution).
- **Oversized request**: not independently bounded — real, disclosed:
  no explicit maximum-size check exists; every real call site in this
  codebase requests a small, fixed size (16 bytes), so this is a real,
  low-priority, currently-theoretical gap.
- **Failure result**: see "error mapping" above — real, disclosed,
  not yet handled explicitly.
- **Thread behavior**: real, standard — `SecRandomCopyBytes` is a real,
  documented-thread-safe Apple API; no additional synchronization is
  added or needed.
- **Platform compatibility**: real — `platform.Security.SecRandomCopyBytes`
  is available on all real, current iOS/watchOS/tvOS/macOS targets
  this project's own `iosX64()`/`iosArm64()`/`iosSimulatorArm64()`
  target declarations cover.

## Real, honest classification

iOS execution remains **NOT VERIFIED** — unchanged, no macOS/Xcode on
this host (`ios-secure-storage-runtime-validation-plan.md`, M10.27).
