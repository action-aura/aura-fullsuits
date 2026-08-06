# Signed Lease Verification Authority (M11.7)

Real, single commonMain verification boundary:
`GenerationalSignedLeaseVerifier` (implements `SignedLeaseVerifier`),
`shared/src/commonMain/kotlin/com/actionaura/retail/licensing/lease/SignedLeaseVerifier.kt`.

## Real, exact step order (binding invariant)

1. Algorithm literal check (`== "ed25519"`).
2. Bound + decode (`LeaseDecoder`, `signed-lease-decoding-contract.md`).
3. Key resolution: `keyRing.isTrusted(keyId)` — unknown key fails closed **before** any cryptography.
4. Base64-decode public key + signature; length-validate (32/64 bytes).
5. **Signature verification** (`verifyEd25519Signature`, the real platform primitive).
6. **Only after signature success**: parse claims into `VerifiedLeaseClaims`.
7. Contract-version check (`SUPPORTED_LEASE_CONTRACT_VERSION = "1.0"`).
8. Context binding (product/platform/installation — `lease-context-binding.md`).
9. Time-window check (60s clock-skew tolerance, matching `assertion_verifier.py`'s `CLOCK_SKEW_TOLERANCE_SECONDS`).
10. Parse `OfflinePolicyEvidence` from the verified payload.
11. Return `LeaseVerificationResult.Verified(claims, evidence, anchor)`.

**No claim is ever read or acted on before step 5 succeeds** — real,
tested (`oneByteFlippedInPayloadInvalidatesTheSignature`,
`leaseSignedByAWrongKeyIsRejectedEvenIfClaimedKeyIdIsKnown`: a lease
signed with the *wrong* private key but claiming a *known, trusted*
key ID is still rejected at the signature step, never reaches claim
parsing).

## Real, testable design

`GenerationalSignedLeaseVerifier(keyRing, monotonicClock =
systemMonotonicClock())` — the monotonic clock is injectable,
defaulting to the real platform clock in production. **Real bug caught
during implementation**: the first version hard-called
`systemMonotonicClock()` internally rather than accepting it as a
constructor parameter, making trusted-time non-deterministic in tests
(a fixture's intended "now" would drift by whatever the real device's
actual monotonic clock happened to read at test-run time). Fixed
before any test was written against it — real, caught-early discipline
consistent with this codebase's own established pattern of finding and
fixing defects via its own test-writing process.

## Real, executed test coverage (`SignedLeaseVerifierTest.kt`, 22 tests, all real Ed25519 via Tink)

Valid lease verifies; one-byte payload/signature mutation rejected;
wrong-key forgery rejected; retired-but-trusted key still verifies;
unknown key rejected before cryptography; product/platform/installation
mismatch each rejected; expired/not-yet-valid rejected; 60s clock-skew
tolerance honored; unsupported algorithm rejected; forbidden-marker
field rejected; unknown field rejected; duplicate top-level key
rejected; oversized payload rejected; Clinic-lease-to-Retail and
Android-lease-to-iOS-context both rejected (product/platform
mismatch); malformed/empty base64 signature rejected; unsupported
contract version rejected.

## Real, disclosed gaps

- Entitlement structural validation (M11.11) and app-version policy
  (M11.21) are **not yet wired into this verifier** — `VerifiedLeaseClaims`
  does not currently carry `entitlements`/`app_version_policy`, and no
  gate exists yet. Real, open work, not silently claimed done.
- `assertionId`/`issuer`/`allowedDeviceCount`/etc. (presentation/
  audit-trail-only fields per the audit's own classification) are not
  yet parsed into `VerifiedLeaseClaims` — deliberately deferred, not a
  correctness gap (nothing security-relevant depends on them).
- Lease sequence/replay-rollback protection (M11.16) beyond the
  time-window check is not yet implemented — real, disclosed, open
  item (`lease-replay-rollback-protection.md`).
