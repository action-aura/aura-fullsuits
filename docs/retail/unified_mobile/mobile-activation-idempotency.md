# Mobile Activation Idempotency (M9.11)

`ActivationIdempotencyCoordinator` (`shared/.../licensing/transport/
ActivationIdempotencyCoordinator.kt`) — the mobile-side half of
idempotency; the server remains the final authority
(`activation-idempotency-contract.md`, M7.10).

## Real behavior

- **One stable key per logical attempt**: `keyFor(requestFingerprint)`
  returns the existing key when the fingerprint matches the in-flight
  one, mints a fresh key otherwise — real, `Mutex`-protected.
- **Duplicate UI taps reuse the same in-flight command**:
  `tryBeginAttempt()` is a real compare-and-set guard — a second call
  while `inFlight == true` returns `false` and does nothing.
- **Retry after timeout reuses the same key**: as long as the caller
  presents the same `requestFingerprint` (a hash of the real
  `ActivationCommand` contents, computed by the caller), `keyFor`
  returns the identical key — matching the legacy `OwnerClient`'s own
  real, tested "fresh nonce, same idempotency key" pattern
  (`mobile-licensing-network-audit.md`).
- **A materially changed request receives a new key**: a different
  `requestFingerprint` mints a fresh key (real, direct consequence of
  the `keyFor` logic — never reuses a stale key for different content).
- **Concurrent activate actions cannot launch conflicting duplicate
  requests**: the same `Mutex` serializes both `keyFor` and
  `tryBeginAttempt` calls.
- **Cancellation does not silently create a second logical attempt**:
  `completeAttempt()` must be called (by the orchestrating ViewModel,
  in a `finally` block) before a new attempt can begin; `reset()`
  exists as the real, explicit "abandon this logical attempt entirely"
  operation, used only on a genuinely new user-initiated flow (e.g.
  after `RESTART`).

## Real key material

Never used as an authentication credential (`keyGenerator: () ->
String` is caller-injected — production wiring supplies a real random-
UUID generator, tests supply a deterministic one) — and never logged
at sensitive verbosity (it is a plain, non-secret string; M9.23's
redaction audit confirms it is not on the redacted-fields list because
it carries no PII/credential value itself, matching the real server-
side treatment of `idempotency_key` in `owner/contracts/
activation-request-v1.schema.json`, which is not itself masked).

## Process-recreation recovery contract

Non-secret retry metadata (the current key + fingerprint) may be
persisted through a future safe, non-secret abstract store — not
implemented in M9 (real secure credential persistence is M10's own
scope); see `activation-process-recovery-contract.md` for the full
real recovery design.
