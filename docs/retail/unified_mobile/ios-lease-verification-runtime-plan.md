# iOS Lease Verification Runtime Plan (M11.40)

Real, honest plan — not a report of execution. No macOS/Xcode exists
on this host; `SignedLeaseSignatureVerifier.ios.kt`,
`MonotonicClock.ios.kt`, and `LeaseCanonicalJson.ios.kt` have never
compiled, linked, or run.

## Real, written iOS source (this milestone)

- `SignedLeaseSignatureVerifier.ios.kt` — real `platform.Security`
  `SecKey`-based Ed25519 verification (real compile-time API-name
  uncertainty disclosed in `platform-crypto-adapter-report.md`).
- `MonotonicClock.ios.kt` — real `NSProcessInfo.systemUptime`-based
  monotonic clock.
- `LeaseCanonicalJson.ios.kt` — real `NSString.
  precomposedStringWithCanonicalMapping`-based NFC normalization.

## Real validation plan for a future macOS/Xcode-equipped session

1. `./gradlew :shared:compileKotlinIosSimulatorArm64` — first real proof the three files above resolve against real Apple SDK headers; **fix the exact `SecKeyAlgorithm` constant if the real compiler rejects `kSecKeyAlgorithmEdDSASignatureRFC8032`** (the one real, disclosed compile-time-uncertainty item).
2. Run the existing, unmodified `commonTest`/`androidUnitTest`-equivalent suite (`LeaseCanonicalJsonTest`, `OfflinePolicyEvaluatorTest` — both real Kotlin/Native-portable as written, no JVM-only APIs) via `:shared:iosSimulatorArm64Test`.
3. Port `SignedLeaseVerifierTest.kt`'s real Ed25519 test-signing logic to an iOS-reachable equivalent (Tink is JVM/Android-only; a real iOS-side signing helper for test-fixture generation must use `SecKeyCreateRandomKey`/`SecKeyCreateSignature` or a portable pure-Kotlin/Native Ed25519 implementation — real, open design choice for that future session, not resolved here).
4. Real round-trip: sign a real fixture with a real test Ed25519 key on-device/simulator, verify through the real `verifyEd25519Signature` iOS actual, confirm `true`.
5. Real tamper rejection: mutate one byte of a real signed payload, confirm `false`.
6. Real NFC-normalization cross-check: canonicalize a payload containing a non-ASCII string on iOS, compare byte-for-byte against the same payload's Kotlin/JVM-side and real Python-side canonical output (extending `LeaseCanonicalJsonTest`'s own real cross-Python evidence to a third real runtime).
7. Real monotonic-clock behavior across app background/foreground and device lock — confirm `systemUptime` behaves as documented across a real process lifecycle, not just in source.
8. Wire into the real iOS secure-storage integration (`ios-secure-storage-runtime-validation-plan.md`, M10.27's own still-open plan) once both exist together.

## Honest classification

**iOS lease verification: NOT VERIFIED.** Source-written, structurally
aligned with this codebase's own established iOS cinterop patterns,
zero real Kotlin/Native/Security-framework execution on this host —
the same honest disclosure standard every prior milestone in this
branch has applied to iOS-specific claims.
