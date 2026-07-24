# Phase 7V-F — Final Closure Handover

## What this phase achieved

A physical Android device connected and authorized successfully, enabling real proof of Clinic's
signed rc.1→rc.2 upgrade with data preservation. While driving the required live, real-elapsed-time
offline/restricted-mode validation (on Windows, since the device disconnected before the Android
portion could be completed), this session found and fixed **a genuine, previously-undiscovered P0
defect** in the core offline-enforcement mechanism: the trusted-time anchor was being re-pinned
fresh on every check-in evaluation instead of once per continuous sync, which silently prevented
any continuously-running installation from ever detecting an extended Owner outage, no matter how
much real time passed. This defect existed since Phase 6/7 and was invisible to every prior unit
test (which only ever seeded a single snapshot and evaluated once) — it was only found because this
phase's spec required genuine live, real-elapsed-time testing, exactly the kind of validation this
phase exists to perform. It is now fixed, tested (4 new regression tests), and verified live on
both products reaching real `RESTRICTED` state after a genuine 100-second Owner outage.

Two further real defects were found and fixed along the way: Android's `app.py` crashed entirely on
launch (`ModuleNotFoundError('cryptography')`, from an eager Windows-only import), and the
`cryptography` package itself was never declared as an Android build dependency despite being
required by shared assertion-verification code.

## What remains open

The physical Android device did not stay connected reliably for the remainder of the session
(disconnected 3+ times, confirmed genuine via clean `adb` daemon restarts each time, not a stale
state). Retail's physical upgrade/lifecycle was not started; Clinic's physical lifecycle beyond the
initial signed upgrade and data verification was not completed. See `remaining-gate-matrix.md` for
the itemized breakdown and `final-residual-risk-register.md` for the full risk list.

## Tag status

**No new tag created.** Per the governing spec's explicit Definition of Done, the closing tag
`aura-product-licensing-phase7-validation-complete` requires every mandatory physical gate to pass
— it does not. The existing `aura-product-licensing-integration-phase7-complete` tag remains
exactly as it was, unmoved.

## Recommended next step

Reconnect the Android device (consider a different USB port/cable given the repeated disconnects
this session), confirm via `adb devices -l` showing `device` status stably, then complete: Retail's
full physical upgrade+lifecycle sequence, Clinic's physical activation-onward lifecycle, and the
physical Kotlin/Python authority + Logcat privacy checks for both products. All Windows-side and
Owner-side infrastructure needed for that (the real production-like Owner script, the real trust
anchor, both rebuilt signed APKs) is already in place and does not need to be redone.

## Stop condition

Per the governing spec: stopping completely after Phase 7V-F. No Phase 8, subscription-expiry
enforcement, payment gateways, e-invoicing, Aura Core integration, or VPS/public deployment work
was begun or implied by anything in this session.
