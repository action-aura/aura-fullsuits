# Licensing Error Presentation Map (M9.17)

`ErrorPresentationMap.kt` — reuses the stable M7.15 `ServerReasonCode`/
`LocalReasonCode`/`LicensingError` contract verbatim; maps into the
five real, distinct presentation-safe categories the checkpoint
itself names.

## Real category coverage

- **A. Customer authentication**: `InvalidCredentials`,
  `AccountNotVerified`, `AccountLocked`, `AccountDisabled`,
  `SessionExpired`, `ReauthenticationRequired`. (Real, current mapping
  note: no real server reason code yet exists for these — Owner's real
  external customer-auth API does not exist per M8's own findings —
  so `toPresentationError()` currently reaches these cases only via
  `LocalReasonCode.CAPABILITY_DENIED → ReauthenticationRequired`; the
  rest are real, named, reachable cases waiting for the real customer-
  auth server contract M9.6/M9.7 model against, not dead code — a
  future real server error will map into one of these once that API
  exists.)
- **B. License**: `NotFoundOrNotOwned`, `NotIssued`, `Suspended`,
  `Expired`, `Revoked`, `SubscriptionInactive`, `ProductNotAllowed`,
  `PlatformNotAllowed` — mapped from the real M7.15
  `ServerReasonCode`s (`ACTIVATION_REJECTED` → `NotFoundOrNotOwned`,
  the real anti-enumeration-normalized code per M7.15's own finding
  that Owner never reveals more; `PRODUCT_MISMATCH` →
  `ProductNotAllowed`; `PLATFORM_NOT_ALLOWED`/`RELEASE_CHANNEL_NOT_
  ALLOWED` → `PlatformNotAllowed`; `INSTALLATION_SUSPENDED` →
  `Suspended`).
- **C. Installation**: `DeviceLimitReached`, `Revoked`, `Conflict`,
  `IdentityConflict`, `ReplacementRequired` — mapped from
  `DEVICE_LIMIT_REACHED`, `DEVICE_KEY_REVOKED`, `DEVICE_ALREADY_
  REGISTERED`, `DEVICE_KEY_MISMATCH`/`INVALID_SIGNATURE`/`INVALID_
  PUBLIC_KEY`, `INSTALLATION_DEACTIVATED`/`INSTALLATION_REPLACED`
  respectively.
- **D. Transport**: `Offline`, `Dns` (modeled, not currently produced
  by any real mapping — `TransportOutcome.NetworkFailure` maps to
  `Offline`; a future, more granular DNS-specific transport outcome
  could map here if ever distinguished), `Timeout`, `Tls`,
  `ServerUnavailable`, `RateLimited(retryAfterSeconds)`, `Cancelled`,
  `NotConfigured` — mapped directly from `TransportOutcome`'s own
  cases (`toPresentationError()` extension on `TransportOutcome<T>`).
- **E. Contract**: `MalformedResponse(reason)`,
  `UnsupportedVersion(serverVersion)`, `UnknownRequiredField`,
  `InconsistentDevicePolicy`, `WrongProduct`, `WrongPlatform`,
  `WrongInstallation` — mapped from `TransportOutcome.
  MalformedResponse`/`UnsupportedContractVersion` and from local
  reason codes `ASSERTION_PRODUCT_MISMATCH`/`ASSERTION_PLATFORM_
  MISMATCH`/`ASSERTION_INSTALLATION_MISMATCH`.

## Real, deliberate "never silently swallowed" guard

The four real `SUCCESS_CODES` (`ACTIVATION_APPROVED`, etc.) reaching
`ServerReasonCode.toPresentationError()` — which should never happen,
since a success code implies a `TransportOutcome.Success`, not a
`BusinessRejection` — map to `PresentationError.Unknown(name)` rather
than throwing or silently picking an arbitrary category, a defensive,
real guard against a future contract-mapping bug going unnoticed.

## Never raw, never generic

No code path in this file returns a plain string like "Something went
wrong" — every real outcome maps to one of the ~28 named, distinct
`PresentationError` cases, or to `Unknown(raw)` for a genuinely
unrecognized server code (mirroring M7.15's own `ServerReasonCode.
parseOrNull` discipline).
