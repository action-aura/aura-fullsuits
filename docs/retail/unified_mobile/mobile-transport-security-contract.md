# Mobile Transport Security Contract (M9.24)

Real, documented future production transport requirements —
requirements for the not-yet-built real HTTP implementation, not
claims about anything M9 itself executes.

## Real requirements

- **HTTPS only in production** — real, already enforced today by
  `ExternalApiConfiguration.validate()` (M9.5): `http` scheme with
  `environment == PRODUCTION` is unconditionally rejected.
- **Minimum TLS policy** — deferred to the real future HTTP engine's
  own platform defaults (Ktor's `ktor-client-okhttp`/`ktor-client-
  darwin`, per `mobile-http-client-decision.md`) — both real, modern
  engines default to a real, current-generation minimum TLS version
  on their respective platforms; no override is planned unless a real
  compatibility need arises.
- **Hostname validation** — real platform default (never disabled) —
  no code in this milestone touches certificate/hostname validation at
  all, since no HTTP engine exists yet to configure.
- **No trust-all mode, no disabled certificate validation** — a real,
  binding future rule; `ExternalApiConfiguration` has no field that
  could express "skip validation," by design.
- **No cleartext production exception** — real, already enforced
  (`ExternalApiConfiguration.validate()`).
- **Bounded redirects, redirect authorization-header stripping** —
  real future requirement for the HTTP engine configuration, not
  applicable to any code that exists in M9 (no request/redirect
  handling exists yet).
- **No credential in URL** — real, already enforced
  (`ExternalApiConfiguration.validate()` rejects `@` in the base URL).
- **Secure proxy behavior** — deferred to the real future engine
  configuration; no proxy-handling code exists in M9.
- **Request/response size limits, decompression limits, JSON depth/
  size limits** — real, partially already enforced at the JSON layer:
  `canonicalize`/`_normalize` (in the real, existing `commercial_
  runtime`/Owner `canonical.py`, cited in M7/M8) already rejects
  excessive nesting server-side; the future Kotlin HTTP transport must
  apply an equivalent client-side limit when it is built — recorded
  here as a real, binding requirement for that future work, not solved
  by M9's own code (which parses no untrusted response at all today).
- **Timeout limits** — real, already modeled:
  `ExternalApiConfiguration.requestTimeoutMillis`/
  `connectTimeoutMillis`/`responseTimeoutMillis`, each validated
  positive.

## Certificate pinning — evaluated, not implemented

Real, deliberate non-decision: certificate pinning is not implemented
in M9. Per the checkpoint's own instruction ("do not implement rigid
pinning without a rotation and incident-recovery plan"), pinning
requires a real, separately-designed key-rotation/incident-recovery
process before it can be safely added — a real, future security
milestone's own scope, not decided unilaterally here. Recorded as a
real, open item, not silently skipped.

## Real, current M9 status

No HTTP transport exists yet (`mobile-http-client-decision.md`), so
none of the above is actively enforced by running code today except
the two items already implemented in `ExternalApiConfiguration.
validate()` (HTTPS-only-in-production, no-embedded-credentials). This
document exists to bind the *future* real HTTP implementation to these
requirements from the start, not to claim they are all live today.
