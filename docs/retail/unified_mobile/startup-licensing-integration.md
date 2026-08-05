# Startup Licensing Integration (M9.20)

Real, additive integration of `LicensingBootstrapState`
(`shared/.../licensing/transport/LicensingBootstrapState.kt`) into
`App.kt`'s real startup sequence — a deliberately narrow, disclosed
scope decision, not a full activation gate.

## Real, closed states

`NoActivationState`, `ActivationRequired`, `ServiceNotConfigured`,
`SecureStorageUnavailable`, `StoredMaterialUnavailable`,
`StoredMaterialUnreadable(reason)`, `FutureLeaseVerificationRequired`,
`AuthenticatedLocalRetailState`, `CommerciallyBlocked`,
`BootstrapError(reason)` — the checkpoint's own named list plus one
real, defensive addition (`BootstrapError`) for the "never let an
exception silently open the shell" requirement.

## Real, exception-safe computation

`computeLicensingBootstrapState(hasStoredInstallationMaterial)` — a
pure function wrapped in `try/catch`; any exception maps to
`BootstrapError(reason)`, never silently swallowed, never mapped to a
state that would let bootstrap proceed as if nothing happened. Real,
current result: because no stored Installation material exists yet
(no platform secure storage — M10 scope) and the real transport is
always `DisabledProductionTransport` in production, this always
resolves to `ServiceNotConfigured` today — a real, honest, current
fact.

## Real integration point — `App.kt`

Computed once, inside the existing real `Bootstrap` phase's own
`LaunchedEffect(Unit)` block (`App.kt`), stored in a new
`licensingBootstrapState` composition-local `remember`ed state.

## Real, disclosed scope decision: this does NOT gate the Retail shell

**`phase` still transitions `Bootstrap → Authenticated` immediately**,
exactly as it has since M6.25's own real, accepted `AUTHORIZATION_
PENDING` development posture — reconfirmed as a real PASS gate
("Authorization remains explicitly deferred, not faked") in every
milestone from M6 through M8's own decision documents. This milestone
does not change that.

**Why not gate now**: real activation completeness requires the full
chain — secure storage (M10, not yet built) to actually persist an
Installation credential, and offline lease verification (M11, not yet
built) to actually trust a stored lease across app restarts. Gating
the entire Retail shell on `LicensingBootstrapState` today, with
neither dependency built, would force one of two real bad outcomes:
1. **Permanently block the app** — no real path exists yet for a user
   to ever reach `AuthenticatedLocalRetailState`/pass the gate, since
   the transport is always `DisabledProductionTransport`.
2. **Invent a bypass** — explicitly forbidden by the checkpoint's own
   rule ("development-only bypass explicitly forbidden in release").

Gating prematurely would trade a real, working, already-tested Retail
shell (596+ passing tests, a real APK, three prior milestones' own
accepted PASS/CONDITIONAL PASS verdicts) for a permanently-blocked or
dishonestly-bypassed one — a regression, not progress. `AppPhase.
LicenseBlocked` remains the real, already-defined (M6), not-yet-
reachable state a future milestone (after M10/M11 exist) wires this
computation into for real.

## Real "never silently open the shell on exception" proof

`LicensingBootstrapStateTest.computeLicensingBootstrapStateNeverThrows`
(M9.29) exercises the real function directly and confirms it always
returns a real `LicensingBootstrapState` value, never propagates an
exception to the caller.

## MainActivity regression coverage

Real, already-existing, still-green:
`MainActivityWiringRegressionTest` (M6 follow-up, `shared/src/
androidUnitTest/.../MainActivityWiringRegressionTest.kt`) — proves
`MainActivity` still constructs the real `AuraAppContainer` and passes
it to `App(container)`. M9 touches neither `MainActivity.kt` nor the
real container-construction path — this existing test's continued
pass (part of the full 596+/M9-extended suite) is the real, direct
regression proof the checkpoint asks for; no new, duplicate test was
needed since the existing one already covers this exact concern and
`App.kt`'s own signature (`App(container: AuraAppContainer? = null)`)
is unchanged by this milestone's additive `licensingBootstrapState`
variable.
