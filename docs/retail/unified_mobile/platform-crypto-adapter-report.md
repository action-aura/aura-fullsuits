# Platform Crypto Adapter Report (M11.8)

## Android — real, tested, host-executable

`SignedLeaseSignatureVerifier.android.kt` — real Google Tink
`Ed25519Verify` call, wrapped to the codebase's own closed `Boolean`
contract (never throws; wrong-length key/signature returns `false`
before even calling Tink). **Real, executed, host-verifiable**: Tink's
raw `subtle` primitives are plain JVM classes with no
`AndroidKeyStore`/Android-runtime dependency, so
`SignedLeaseVerifierTest.kt`'s 22 tests exercise this exact real code
path on this Windows host's JVM via `:shared:testDebugUnitTest` — not
a mock, not simulated, the real cryptographic library actually
verifying real signatures. `AndroidKeyStore` itself is not used here
(would require API 33+ for native Ed25519, above minSdk 26 — same real
constraint the legacy Android app's own `DeviceIdentity.kt` already
documents and works around).

**What remains unverified**: real on-device behavior (thread
safety under real Android's own process model, real interaction with
`ProGuard`/R8 minification of the Tink dependency in a real release
build, real APK size impact) — no Android device/emulator exists on
this host. `:androidApp:assembleDebug` is `BUILD SUCCESSFUL` with the
new Tink dependency resolved and packaged, confirmed real evidence the
dependency itself is compatible with this project's build
configuration.

## iOS — real, written, NOT VERIFIED

`SignedLeaseSignatureVerifier.ios.kt` — real Kotlin/Native cinterop
against `platform.Security`'s `SecKey` API
(`kSecAttrKeyTypeEd25519`/`kSecKeyAlgorithmEdDSASignatureRFC8032`),
matching this codebase's own established cinterop pattern (M9's
`SecureRandomBytes.ios.kt`). CryptoKit itself (`Curve25519.Signing`,
Apple's more idiomatic modern API for this) is a Swift-native
framework without a stable C header, not directly reachable from
Kotlin/Native the way `platform.Security` already is — the same real
constraint governing every other iOS cinterop file in this codebase.

Classification, per M11.8's own required labels:
```
IOS_SIGNATURE_VERIFIER_SOURCE = IMPLEMENTED
IOS_SIGNATURE_VERIFIER_COMPILE = NOT VERIFIED
IOS_SIGNATURE_VERIFIER_RUNTIME = NOT VERIFIED
```

**Real, additional, honest disclosure beyond the standard NOT-VERIFIED
pattern**: this specific file carries a real compile-time-uncertainty
risk beyond the usual runtime-only uncertainty every other iOS file in
this codebase discloses — the exact `SecKeyAlgorithm` constant
name/availability for raw Ed25519 verification could not be confirmed
against real Apple SDK headers from this Windows host, and may require
correction the first time this file is actually compiled on macOS/
Xcode (`signed-lease-cryptography-decision.md`, `ios-lease-
verification-runtime-plan.md`). This is a real, structural attempt at
the correct API, not a placeholder — but its exact correctness is
genuinely unconfirmed, not merely "unrun."

## Real, closed algorithm allowlist enforcement

Both platforms share the identical `expect fun verifyEd25519Signature`
contract (`SignedLeaseSignatureVerifier.kt`) — no platform-specific
algorithm negotiation exists; `GenerationalSignedLeaseVerifier` itself
rejects any `algorithm != "ed25519"` before ever calling either
platform primitive (real, tested: `unsupportedAlgorithmIsRejected`).
