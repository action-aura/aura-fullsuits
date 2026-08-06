# Signed Lease Cryptography Decision (M11.2)

Per M11's own explicit instruction, the algorithm is **not** a new
choice — it is dictated by the canonical executable authority audited
in `canonical-signed-lease-authority-audit.md`.

## Chosen algorithm

**Ed25519** (RFC 8032), raw 32-byte public keys, raw 64-byte
signatures — no ASN.1/DER wrapping, no algorithm negotiation. Confirmed
from two independent, cross-verified real implementations already in
this codebase:

- Server/verification side: Python `cryptography` library,
  `cryptography.hazmat.primitives.asymmetric.ed25519.Ed25519PublicKey`.
- Android device-signing side (a different key, same algorithm):
  Google Tink's raw "subtle" primitives,
  `com.google.crypto.tink.subtle.Ed25519Sign`/`Ed25519Verify`
  (`android/aura-retail/.../licensing/DeviceIdentity.kt`), physically
  cross-verified against the Python side via a real
  `Ed25519CrossVerifyTest` — not merely assumed compatible.

## Closed algorithm allowlist

Exactly one value is ever accepted: the literal string `"ed25519"` in
the envelope's `algorithm` field. No fallback, no "none" algorithm, no
accepting one key type under a different declared algorithm. Any other
value fails closed with a real, specific reason code — matching the
canonical Python authority's own `algorithm != "ed25519"` gate
exactly.

## Implementation authority per platform

| Platform | Library | Rationale |
|---|---|---|
| Android | **Google Tink** (`com.google.crypto.tink:tink-android`), the same raw `Ed25519Sign`/`Ed25519Verify` "subtle" primitives the legacy Android app already uses and has physically cross-verified | `AndroidKeyStore`'s own native Ed25519 support requires API 33+, above this project's minSdk 26 (`shared/build.gradle.kts`) — the same real constraint that drove the legacy app's own decision. Reusing the already-proven library avoids re-litigating a cross-verification problem this codebase has already solved once. |
| iOS | **Apple CryptoKit**, `Curve25519.Signing.PublicKey`/`.verify(_:signedData:)` | Native, available iOS 13+, RFC 8032-compliant raw-key Ed25519 — no legacy iOS reference exists in this codebase (no prior iOS app), so this is a real, new, from-the-Apple-platform-authority decision, not a port. |
| JVM/common test | Real Ed25519 signing via a small, test-only, deterministic keypair generator (no production library dependency added to `commonTest` beyond what `expect`/`actual` already requires) — verification itself always runs through the real `expect`/`actual` platform primitive being tested (Android's real Tink call on `androidUnitTest`), never a mocked verifier | Matches this codebase's own established discipline (M9's `secureRandomBytes` `expect`/`actual`, M10's `SecureBlobStore` `expect`/`actual`) of testing the real platform primitive, not a fake standing in for it. |

## Key format

Raw 32-byte Ed25519 public key, base64-encoded in the envelope/trust
anchor exactly as the canonical Python authority already stores it
(`commercial_runtime/licensing_contracts/trust_anchor.json`:
`"public_key": "uYJu57ljNL0VK8SFP883z5J/ZQg9YqDDNZrSsX6eYcU="`). No
PEM/DER wrapping anywhere in this pipeline.

## Signature format

Raw 64-byte Ed25519 signature, base64-encoded in the envelope's
`signature` field.

## Error mapping

Every real failure mode maps to a closed `SignedLeaseFailureCode`
(see `signed-lease-decoding-contract.md`/`signed-lease-verification-authority.md`)
— malformed base64, wrong-length key, wrong-length signature, and
`InvalidSignature`-equivalent (verification returning `false`/throwing
on the platform primitive) all fail closed to a specific, non-secret
code. Mirroring the canonical Python authority: a bad signature never
throws a raw platform exception up to a caller — it is caught and
mapped.

## Runtime-validation limitations (honest, standing)

- **Android**: real Tink dependency, real code, compiles against the
  Android target — **not executed on a real Android device or
  emulator** (none exists on this host, same standing M10 limitation).
- **iOS**: real CryptoKit-based source — **never compiled or executed**
  on macOS/Xcode (none exists on this host, same standing M10
  limitation). Classified per M11.8's own required labels:
  `IOS_SIGNATURE_VERIFIER_SOURCE = IMPLEMENTED`,
  `IOS_SIGNATURE_VERIFIER_COMPILE = NOT VERIFIED`,
  `IOS_SIGNATURE_VERIFIER_RUNTIME = NOT VERIFIED`.
- **JVM/commonTest**: real, executed, host-verifiable — signature
  verification logic itself (claim parsing, context binding, time
  evaluation, decision model) is exercised via `androidUnitTest`
  against the real Android Tink primitive, which *does* run on this
  JVM host (Tink's Java/Kotlin implementation is pure-JVM, not
  Android-runtime-dependent, unlike `AndroidKeyStore`) — this is real,
  executed, host-verifiable cryptographic verification, not a mock.
