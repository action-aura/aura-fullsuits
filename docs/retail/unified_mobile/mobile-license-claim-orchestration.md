# Mobile License Claim Orchestration (M9.8)

`LicenseClaimOrchestrator`/`LicenseSerialSanityCheck`
(`shared/.../licensing/transport/LicenseClaimContracts.kt`,
`LicenseClaimOrchestrator.kt`).

## Real sequence implemented

Authenticated external Customer session (`ExternalCustomerSessionId`,
supplied by the caller) → License serial entered → **local format
sanity check** (`LicenseSerialSanityCheck.isPlausible`: non-blank,
8-64 chars, uppercase-alnum-plus-hyphen only — a real, narrow
structural check, never an ownership decision) → `LicenseClaimRequest`
construction → `transport.claimLicense(...)` → real
`LicenseClaimOutcome` (`Claimed`/`LocallyImplausible`/`Rejected`).

## Real rules enforced

- **Server remains authoritative**: `LicenseSerialSanityCheck` never
  returns anything resembling "owned"/"valid" — only "plausible enough
  to submit" or not. Real ownership is entirely `transport.
  claimLicense`'s own (currently `TransportNotConfigured`) result.
- **Serial never retained longer than required**: `LicenseClaimRequest`
  holds the trimmed serial only for the duration of the request object
  itself; `LicenseClaimOrchestrator.claim` has no field storing it
  between calls.
- **Serial never enters logs**: `LicenseClaimRequest.toString()` is
  overridden to redact `licenseSerial`.
- **Serial never enters navigation arguments**: no `AuraRoute` type
  references `LicenseClaimRequest`/a raw serial string.
- **Serial never a long-term credential**: `LicenseClaimResult` carries
  only `licensePublicId` (the real, server-assigned opaque identifier,
  matching `AssertionPayload.licensePublicId`, M7.17) and the resolved
  device policy — never the original serial.
- **Idempotency**: `claimLicense` calls flow through the same real
  `ActivationIdempotencyCoordinator` mechanism used for activation
  itself when wired by presentation code (M9.11) — this orchestrator
  does not duplicate idempotency logic, it is the caller's
  responsibility to key retries consistently, per M9.11's own
  contract.
- **Enumeration-resistant errors**: real business rejections flow
  through `toPresentationError()` (M9.17), which reuses the already-
  audited, anti-enumeration-safe `ServerReasonCode`/`ACTIVATION_
  REJECTED` normalization (M7.15) — this orchestrator never attempts
  to infer more than the server actually reveals.

## Real test-only usage

Because the real API is absent, `LicenseClaimOrchestrator` is only
ever exercised in tests against `ContractFixtureTransport` (M9.28) —
production callers always observe `LicenseClaimOutcome.Rejected` with
`PresentationError.Transport.NotConfigured`.
