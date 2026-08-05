# Mobile Network Retry Policy (M9.15)

`RetryPolicy`/`withRetry` (`shared/.../licensing/transport/
RetryPolicy.kt`) — built directly from the legacy `OwnerClient.kt`'s
own real, tested behavior (`mobile-licensing-network-audit.md`), not
invented from scratch.

## Real, bounded policy

`maxRetries` default 4 (bounded `0..10` by `init` requirement — never
unbounded), `baseBackoffMillis` 1000, `maxBackoffMillis` 30 000,
`jitterFraction` 0.25 — the exact same real defaults as the legacy
client's own `OwnerClientConfig`.

## Real retry-eligibility rule

`TransportOutcome.isSafeToRetry()` (`TransportOutcome.kt`) — `true`
only for `RateLimited`, `Timeout`, `NetworkFailure`. Every other
outcome (`Success`, `BusinessRejection`, `AuthenticationRejection`,
`TlsFailure`, `MalformedResponse`, `UnsupportedContractVersion`,
`Cancelled`, `TransportNotConfigured`) is `false` — matching the
checkpoint's own explicit "do not automatically retry" list exactly:
invalid credentials (`AuthenticationRejection`), License not owned/
suspended/expired/revoked/platform-not-allowed/device-limit-reached
(all real `BusinessRejection` shapes), malformed response, unsupported
contract version, idempotency conflict (also a `BusinessRejection`
shape, per `ServerReasonCode.IDEMPOTENCY_CONFLICT`).

## Real backoff behavior

`RetryPolicy.backoffFor` honors a real `Retry-After`-equivalent value
(`retryAfterSeconds`, when the outcome is `RateLimited`) exactly like
the legacy client; otherwise computes bounded exponential backoff with
jitter, capped at `maxBackoffMillis` — never grows unbounded.

## `withRetry`

A real, generic suspend function: retries only while `isSafeToRetry()`
holds, stops immediately on the first non-retryable outcome, and never
exceeds `maxRetries` real attempts — the UI is never blocked by a
hidden, indefinite retry loop.

## Explicit UI honesty requirement

Real requirement carried into M9.18: retry state (attempt count,
whether a retry is currently scheduled) must be exposed in
`ActivationUiState`, never hidden behind a single opaque "loading"
flag — matching `mobile-activation-state-machine.md`'s own "no single
generic loading boolean" rule.
