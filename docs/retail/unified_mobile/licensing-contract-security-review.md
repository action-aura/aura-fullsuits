# Licensing Contract Security Review (M7.21)

Real threat-model review of the licensing contract audited in M7.
Scope discipline: M7 only documents/tests contract behavior; platform
storage/transport implementation is later milestones' work — this
review evaluates the real *contract* (server behavior + shared
models), not a not-yet-built mobile transport/storage layer.

| # | Threat | Real mitigation found | Evidence | Residual risk (deferred, not fixed in M7) |
|---|---|---|---|---|
| 1 | License-key enumeration via distinguishable error responses | Internal reason codes (not-found/suspended/expired/revoked/not-yet-valid, etc.) are normalized to one public `ACTIVATION_REJECTED` before leaving the API boundary | `to_public_reason_code()`, `owner/app/licensing_service/reason_codes.py` | None new — mobile client must never attempt to infer internal state from `ACTIVATION_REJECTED` alone (`licensing-error-contract.md`). |
| 2 | Replay of a captured activation/check-in/deactivation request | Single-use `nonce` + timestamp window, per-request Ed25519 signature over the canonical payload | `activation.py` nonce/timestamp checks; `canonical.py` | Local secure nonce/timestamp generation on the mobile side is future `ANDROID_ADAPTER`/`IOS_ADAPTER` work. |
| 3 | Tampered signed assertion accepted by a client | Client-side `verify_assertion()` fully re-verifies signature, trust, product/platform/installation/device-fingerprint match, and time window — never trusts a prior verification performed elsewhere | `assertion_verifier.py:130-247` | Real, but the Kotlin port of this verifier doesn't exist yet (gap 7, `licensing-gap-ownership-matrix.md`) — M7.17 models the data shape only. |
| 4 | Rogue/forged Owner signing key accepted by a client | `OwnerTrustStore` only admits a rotation manifest signed by an already-trusted key; a bundled first-run anchor seeds the initial trust | `trust_store.py:76-121`, `trust_anchor.json` | Same as #3 — the store itself isn't ported to Kotlin yet. |
| 5 | Device-key compromise (stolen private key) used to keep an installation "alive" after detection | Explicit device-key revoke/replace path — revoked key never trusted again, even retroactively | `device_identity.py:112-139`, `assertions.py:123-124` | Detection/reporting UX for "my device may be compromised, revoke it" is a future staff/support-tooling concern, out of M7 scope. |
| 6 | Final-device-slot race condition granting more installations than `device_limit` | Real row-level lock (`SELECT ... FOR UPDATE`) around the whole check-then-write | `activation.py:175-177`, proven by `test_phase6_concurrency.py` | None found. |
| 7 | Idempotency-key reuse to smuggle a different request through as if it were a retry | `IDEMPOTENCY_CONFLICT` (409) on any fingerprint mismatch for a reused key | `idempotency.py:38-40` | None found. |
| 8 | Invasive device fingerprinting for cross-app tracking | No real hardware identifier (IMEI/serial/MAC/advertising ID) exists in any real schema or contract; `fingerprint_hash` is nullable, unpopulated, and its own model comment explicitly disclaims raw-HW-ID use | `installation-identity-privacy-contract.md` | Future mobile implementation must continue honoring this — a later milestone populating `fingerprint_hash` with a real hardware ID would be a real regression against this review's own finding. |
| 9 | Prohibited business data (Products/Customers/Inventory/Sales/etc.) leaking to Owner via the licensing channel | `additionalProperties: false` schemas, real Phase 6 forbidden-term scan across live HTTP responses, M7.17 models are closed data classes with no open `Map` field, M7.18 serialization test proves the exact real field set | `owner-data-minimization-contract.md`, `LicensingContractTest.
activationRequestSerializesToExactlyTheRealOwnerAllowlist` | None found. |
| 10 | Plaintext license key appearing in logs/crash reports/toString output | Server: `del`'d immediately post-HMAC-lookup, never logged/persisted/returned (proven end-to-end by a real DB/log/audit grep test). Client (M7.17): `ActivationRequest.toString()` explicitly redacts `licenseKey`/`signature`, proven by `LicensingContractTest.activationRequestToStringNeverExposesLicenseKeyOrSignature` | `external-api-forbidden-data-report.md`; `ActivationContracts.kt` | A real crash-reporting/logging integration (not built yet) must be audited again once it exists, to confirm it doesn't serialize request objects through a path that bypasses `toString()` (e.g. reflection-based crash-report field dumps) — flagged for a later milestone, not resolved by a `toString()` override alone. |
| 11 | Manual-approval device-limit bypass via two concurrently-approved pending activations | Device limit is re-checked at approval time, not just at initial request time | `test_commercial_ops_activation_policy.py::test_approve_pending_activation_rechecks_device_limit` | None found. |
| 12 | Cross-license device-key reuse (a compromised device key activating a second, unrelated license) | Explicitly rejected — confirmed by real test | `test_phase6_activation_protocol.py` ("same device activating a different license rejected cleanly") | None found. |
| 13 | Clock manipulation on the device to extend an expired offline lease | `TrustedTime` rollback detection; `CLOCK_ROLLBACK_SUSPECTED` local reason code; local safety ceiling can only shorten, never lengthen, grace | `trusted_time.py`, `policy_evaluator.py`, `test_trusted_time.py` | Real, but again only exists in the not-yet-ported `commercial_runtime` package, not yet in the Kotlin `commonMain` verifier. |

## Explicit scope boundary honored

This review documents/tests the real *contract* — reason-code
normalization, replay protection, signature verification design, trust
rotation, device-key lifecycle, race-condition locking, idempotency,
privacy-safe identity, and data minimization, all already real and
already tested server-side / in `commercial_runtime`. It does not
design or implement platform secure storage, transport-layer pinning,
or the Kotlin verifier port — those remain explicitly future
milestones per the governing checkpoint's own M7 scope prohibitions,
and are tracked as gaps 7-9 in `licensing-gap-ownership-matrix.md`,
not silently treated as solved by this review.
