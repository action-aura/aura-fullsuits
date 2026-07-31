# Phase 8V-P5 — Local-Deactivation Reactivation UX — Decision

## Question

Prior sessions flagged "no obvious in-app reactivation path after local deactivation" without
classifying it. This session read the actual contract to decide whether that's intended (A: fresh
activation via key required) or a real gap (B: Owner-side reactivation should be possible without
re-entering the key, and no path exists).

## Source evidence

- `commercial_runtime/licensing_contracts/deactivation.py` docstring: "The device private key is
  deliberately NOT destroyed here... this policy is retain. Destruction only happens through the
  explicit device-replacement flow's `destroy_key()` call... never as a side effect of plain
  deactivation, so an accidental deactivation can be undone by **reactivating the same device without
  losing its identity**."
- `commercial_runtime/licensing_contracts/state_machine.py`: `TERMINAL_UNTIL_REACTIVATION =
  frozenset({REVOKED, DEVICE_DEACTIVATED})` -- `DEVICE_DEACTIVATED` is deliberately terminal-until-
  reactivation, and the state machine defines a real transition
  `DEVICE_DEACTIVATED --reactivation_requested--> ACTIVATION_REQUIRED`.
- `android/aura-clinic/.../LicensingScreen.kt` line 220 (confirm-deactivate dialog): "You will need to
  reactivate **with a license key** to use commercial features again." -- the product's own UX copy
  already states intent A explicitly.

## Decision: **A** -- explicit local deactivation intends fresh activation via key

The device identity (private key) is retained specifically so a *re-activation* (same device, same
key material, new key entry) is cheap and doesn't lose the installation's history -- not so that Owner
alone can silently flip the device back on. Requiring the key again is the deliberate proof-of-possession
gate re-applied. This matches option A exactly.

## Real gap found, but classified as P2/UX, not P0/P1

The state machine defines a legitimate `reactivation_requested -> ACTIVATION_REQUIRED` transition, and
the dialog text promises the user can "reactivate with a license key" -- but
`LicensingScreen.kt` only renders the key-entry form when `state in {NOT_CONFIGURED,
ACTIVATION_REQUIRED}` (line 116); `DEVICE_DEACTIVATED` is not in that set, and the only actions shown
in that state are "Check Now" (harmless, but can never leave `DEVICE_DEACTIVATED` on its own) and no
"Deactivate" button (correctly suppressed, line 205). **There is currently no on-screen way to trigger
`reactivation_requested` and reach the key-entry form again** -- the promised recovery path exists in
the contract layer but is not wired into this screen.

Per the governing spec's explicit instruction for case A ("do not change production code, classify as
accepted P2/UX limitation, make app messaging explicit"), **no production code was changed this
session**. This is recorded as a real, disclosed P2 UX limitation for the residual risk register: the
fix (when scheduled) is to add `DEVICE_DEACTIVATED` to the key-entry-form condition, or add an explicit
"Reactivate" button that emits `reactivation_requested` before showing the form -- a small, well-scoped
Kotlin-only change with a corresponding state-machine regression test, deferred to normal backlog
rather than rushed into this validation session.

## Messaging status

The existing dialog copy already states the requirement explicitly ("You will need to reactivate with a
license key to use commercial features again") -- no additional messaging change was required to satisfy
that part of case A's guidance.
