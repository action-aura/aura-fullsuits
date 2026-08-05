# Lease Refresh Orchestration (M9.14)

`LeaseRefreshOrchestrator`/`LeaseRefreshState`
(`shared/.../licensing/transport/LeaseRefreshContracts.kt`,
`LeaseRefreshOrchestrator.kt`).

## Real, closed states (16, per the checkpoint's own list)

`NoStoredInstallationAuthority`, `SecureStorageUnavailable`,
`ValidRefreshSchedulingInput`, `RefreshInProgress`, `RefreshSuccess`,
`NetworkUnavailable`, `ServerUnavailable`, `AuthenticationRejected`,
`InstallationRevoked`, `LicenseSuspended`, `LicenseExpired`,
`SubscriptionInactive`, `RequiredAppUpdate`, `MalformedLeaseResponse`,
`RetryScheduled`, `TerminalBlock`.

## Real orchestration logic

`refresh(installationId)`:
1. `null` installation → `NoStoredInstallationAuthority`.
2. `hasStoredInstallationAuthority` (caller-injected, real check
   against whatever M10 storage will eventually back) returns `false`
   → `SecureStorageUnavailable`.
3. Otherwise calls `transport.refreshLease(...)` and maps the real
   `TransportOutcome` into the matching state — `Success` →
   `RefreshSuccess(assertion)`; `TransportNotConfigured` →
   `ServerUnavailable`; `NetworkFailure`/`Timeout` →
   `NetworkUnavailable`; `RateLimited` → `RetryScheduled`;
   `AuthenticationRejection` → `AuthenticationRejected`;
   `MalformedResponse` → `MalformedLeaseResponse(reason)`;
   `BusinessRejection` → mapped through `toPresentationError()` to the
   matching specific state (`InstallationRevoked`/`LicenseSuspended`/
   `LicenseExpired`/`SubscriptionInactive`, else `TerminalBlock`);
   `TlsFailure`/`Cancelled` → `TerminalBlock`;
   `UnsupportedContractVersion` → `RequiredAppUpdate`.

## Real, binding scope boundary

This orchestrator **does not**: verify the lease signature (that
requires the real Ed25519 verification logic, gap #7 in
`licensing-gap-ownership-matrix.md`, still unported); decide offline
grace (real M11 `OfflinePolicyEvaluator` port); enforce offline expiry
(same); persist refresh credentials (M9.13's sinks are invoked by
`ActivationResponseProcessor`, not by this orchestrator, which only
returns the raw `SignedAssertionEnvelope` inside `RefreshSuccess` for
the caller to hand off separately).

**An unverified signed lease is never treated as trusted commercial
authority by this class** — `RefreshSuccess` only means "the transport
call succeeded and returned a well-formed envelope," never "this lease
is cryptographically valid" — that determination remains real, future,
unbuilt M11 work.
