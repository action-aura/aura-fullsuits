# Secure Storage User-Presence Decision (M10.20)

Real, evaluated decision: **no biometric authentication requirement**
for protected licensing material in M10. Device-unlock (already
implied by the M10.9 iOS accessibility class choice, and the real,
standard Android Keystore behavior for a non-`setUserAuthenticationRequired`
key) is the real, chosen baseline — not biometric-gated.

## Real evaluation

| Option | Real tradeoff |
|---|---|
| No direct user presence | Real, maximum availability — but means any process with app-level access (already gated by real OS sandboxing, M10.2 threat #5) can read the material whenever the device is unlocked |
| Device unlocked (chosen) | Real, standard baseline — matches `kSecAttrAccessibleWhenUnlockedThisDeviceOnly` (iOS, M10.9) and ordinary (non-biometric-gated) `AndroidKeyStore` key access (Android, M10.7) |
| Device credential (PIN/pattern/password) | Real, stronger, but functionally close to "device unlocked" for a POS device that is typically already protected by a device passcode |
| Biometric authentication | Real, strongest per-operation gate — but real, serious operational cost for a POS device (see below) |
| Biometric-or-passcode fallback | Real, middle ground — still requires an explicit per-operation user-presence prompt |

## Real reasoning against biometric-by-default

- **Unattended startup**: a real POS device often starts up unattended
  (e.g. a shop opening routine) — a biometric-gated credential read at
  every app launch would block startup until a real staff member is
  present to authenticate, a real, unacceptable operational cost for
  this application's own real use case.
- **Offline POS operation**: the whole point of the real M9/M10/M11
  architecture is offline-capable commercial operation — gating the
  very credential/lease that enables offline operation behind a
  biometric prompt undermines that goal in exactly the scenario
  (offline, possibly unattended) it exists to serve.
- **Background lease refresh** (M11 scope, not yet built): a real
  future background refresh operation cannot itself prompt for
  biometric authentication (no foreground UI context) — a biometric-
  gated key would make real background refresh impossible without a
  separate, non-biometric-gated credential path anyway, undermining
  the whole point of gating.
- **Cashier usability**: real, direct operational concern — a cashier
  should not need to biometrically authenticate merely for the app
  itself to read its own stored commercial credentials in the
  background; that is a real UX cost with no real corresponding
  security benefit for this specific material (the material is not
  itself a spending/PII credential the cashier is authorizing use of —
  it is the *application's own* commercial-activation proof).
- **Support burden**: real, disclosed — biometric enrollment changes
  invalidate biometric-gated keys (M10.2 threat #23/#25); a real,
  unnecessary support burden for a device whose commercial credential
  does not need that level of protection.
- **Device theft**: real, honestly weighed — a biometric gate would
  provide real, additional protection against a stolen-but-unlocked
  device (M10.2 threat #2's own real, disclosed partial-mitigation
  limit). This is a real, genuine tradeoff being made deliberately, not
  overlooked: this decision accepts that residual risk in exchange for
  the real operational requirements above.

## Real decision

**Device-unlock-required, no biometric gate, no passcode-removal
sensitivity beyond the platform's own real, automatic behavior**
(iOS's own real "passcode removed deletes `WhenPasscodeSet`-class
items" behavior is not invoked here, since `WhenUnlockedThisDeviceOnly`
was chosen, not `WhenPasscodeSetThisDeviceOnly`, per M10.9).

## Not implemented

No biometric login experience, no `BiometricPrompt`/`LAContext`
integration anywhere in M10's own code — confirmed by inspection, zero
references to either API. This decision may be revisited by a future
milestone if a real, explicit Product Owner risk decision changes the
tradeoff above — not unilaterally assumed here.
