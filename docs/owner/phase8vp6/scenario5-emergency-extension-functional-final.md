# Phase 8V-P6 — Scenario 5 (Emergency Extension Functional Effect and Expiry) — Final

## Result: PASS

This closes exactly the gap Phase 8V-P5 disclosed as unfixable within that session: "creating a real,
approved, audited emergency extension currently has no functional effect on a device's local grace
computation." That structural gap is now closed (see `emergency-extension-wiring-design.md`) and
physically, rigorously proven end to end.

## Pre-extension state (real, confirmed)

Same installation as Scenario 3 (`c7150980-d45b-4d14-866b-642fb798dceb`), immediately after that
scenario: subscription `EXPIRED`, license `ACTIVE` (untouched), no active extension, device physically
`Restricted`, `Save Patient` denied with no row created (see `scenario3-commercial-enforcement-final.md`
-- deliberately reused rather than re-manufactured, since it's the same real, live restricted baseline
this scenario needs).

## Extension creation (real, synthetic data, short duration)

Created via the real `create_emergency_extension()` service (the same, now-timezone-correct function
fixed in Phase 8V-P5): id `40e94336-6e2d-4734-94d8-468f7a684680`, `duration_hours=0.05` (~3 minutes),
reason "Phase 8V-P6 Scenario 5 real isolated-effect test (post-wiring-fix)", incident reference
`PHASE8VP6-S5-WIRED`. Real timestamps confirmed correctly aligned to real UTC now (no timezone shift):
`starts_at 2026-07-31 08:35:28.108817+03:00`, `expires_at 2026-07-31 08:38:28.108817+03:00`.

## Isolated functional effect (real, physical, unconfounded)

A real "Check Now" tap produced a fresh assertion. Physical screen changed **Restricted -> Active**.
The real captured wire evidence is unambiguous: `subscription_status: "EXPIRED"` (the subscription
itself is still, genuinely, commercially expired -- nothing about it changed) while
`offline_policy.emergency_extension_allowed: true` and `emergency_extension_until:
"2026-07-31T08:38:28.108817+03:00"` (exactly the real extension's own expiry, matched exactly, not
approximately). The *only* variable that changed between the Scenario 3 restricted check-in and this
one is the extension's existence -- this is what "isolated effect" means, and it is what this test
demonstrates, not merely a coincidental successful-check-in timer reset (which Phase 8V-P5 explicitly,
correctly flagged as a confound in its own first attempt -- this session's subscription-driven
restriction is now independent of any check-in-timer reset, so that particular confound cannot recur).

## Positive functional proof (new this session -- Phase 8V-P5 only had the negative/denial proof)

With the device in the extension-covered `Active` state, a real patient was successfully created
through the actual UI: **"Extension Success Patient"**, real ID `P-1785476289-196`, status `active`,
visible in the real Patients list afterward. This pairs the earlier denial (Scenario 3) with a genuine
positive capability restoration, proving the extension's effect is real and functional, not cosmetic.

## Automatic expiry (real elapsed time, no clock manipulation)

Waited for real wall-clock time to pass the extension's own signed `expires_at`
(`2026-07-31T05:38:28Z`; confirmed via `date -u` before and after, no device clock changed). A real
check-in after expiry produced a fresh assertion with `emergency_extension_allowed: false`,
`emergency_extension_until: null` (the extension's own DB query, `expires_at > now`, naturally stopped
matching -- no explicit "expire" action was needed anywhere, exactly as designed). The physical screen
reverted to **Active -> Restricted** automatically. `subscription_status` remained `"EXPIRED"`
throughout -- the underlying subscription state never changed; only the extension's presence, then
absence, changed the effective local outcome.

## Negative controls

Not separately re-exercised this session (MFA/recent-auth/role checks live at the Owner route layer,
not the service function called directly here for speed; already covered by existing, passing Owner
test suites -- `test_phase8_security_fraud_controls.py` and the route-level RBAC/MFA test suites,
unaffected by this session's changes). Hard cap, no-stacking, and revoked-license rejection are covered
by real, passing unit tests in `test_commercial_ops_emergency_extensions.py` (unchanged this session).

## Disposition

**Full PASS** -- isolated functional effect, positive capability proof, and automatic expiry all
demonstrated physically, with real captured wire evidence removing any ambiguity about causation. This
is the first session across six phase iterations (8V-P through 8V-P6) where this scenario's actual
requirement (a real functional effect, not just an informational assertion field) is met.
