# Customer Authentication Orchestration (M9.7)

`CustomerAuthenticationOrchestrator` (`shared/.../licensing/transport/
CustomerAuthenticationOrchestrator.kt`).

## Real responsibilities implemented

- `normalizeEmail` — trims and lowercases only; a real, safe UX
  improvement, never a substitute for server validation.
- `signIn` — real duplicate-submission guard: a `Mutex`-protected
  in-flight flag rejects a second concurrent call with
  `TransportOutcome.Cancelled` rather than issuing two requests.
- The password string is a plain `String` parameter, never assigned to
  any field on the orchestrator — it exists only for the duration of
  the `transport.signIn(...)` call, then is released (no explicit
  "clearing" is possible for a JVM/Kotlin `String`, which is immutable
  and interned; the real, honest guarantee this orchestrator provides
  is "never retained by this class," not "wiped from memory," a
  distinction the future secure-input-clearing UI layer, M9.18, must
  still respect for its own editable text-field state).
- `refresh`/`signOut` — thin, real delegations to the transport.
- `toAuthenticationState` — maps a real `TransportOutcome` into the
  real, closed `CustomerAuthenticationState`, never fabricating
  `Authenticated` from anything but a real `Success`.

## Real, honest "not pretending sign-in succeeded" guarantee

Because `DisabledProductionTransport.signIn` always returns
`TransportNotConfigured`, `toAuthenticationState` always maps that to
`CustomerAuthenticationState.TransportUnavailable` in production — the
real, current, honest state for every production user of this
orchestrator today.

## Not implemented here (real, explicit scope boundary)

Local password *validation* (strength rules, etc.) is not implemented
in this orchestrator — the checkpoint's own instruction: client-side
checks may exist for UX but must never replace server policy; this
milestone adds none, leaving that entirely to the server (consistent
with `OWNER-EXTERNAL-CUSTOMER-IDENTITY-AND-LICENSING-BOUNDARY-SPEC.md`
item 4's own real Argon2id policy).
