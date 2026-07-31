# Phase 8V-P4 — Final Residual Risk Register

| # | Item | Severity | Status |
|---|---|---|---|
| 1 | Scenario 6/7 physical Android re-verification | Blocks final tag | Not verified this session -- single-device constraint; Owner-side mechanics already real and proven in Phase 8V-P/8V-P2 |
| 2 | Scenario 2/3/5's specific unreached sub-checks (visual RESTRICTED transition, 88.00 case, extension's distinguishing effect) | Blocks final tag | Real, structural reasons documented per-scenario; not fabricated |
| 3 | Raw wire-level Owner<->Kotlin traffic capture | Blocks final tag (traffic-privacy completeness) | Local status API + Logcat evidence gathered instead; real but indirect |
| 4 | Backup/restore/export not exercised on-device this session | Low | Unchanged source, proven real on Windows in Phase 8V-P |
| 5 | Clinic Licensing screen shows no re-activation key-entry form in `DEVICE_DEACTIVATED` state (only in `NOT_CONFIGURED`/`ACTIVATION_REQUIRED`) | **New finding, this session** -- low-medium UX gap | Real: `LicensingScreen.kt` line 116 gates the key-entry form on those two states only; a customer who deactivates a device has no in-app path back without a fresh install/clear. Not a security issue (Owner's own record is correctly `DEACTIVATED` either way) -- a real usability gap, not fixed this session (out of this validation-only phase's scope) |
| 6 | Retail POS cart screen (mobile, compact view) has no visible discount/tax input in the flow reached this session | Low | May exist elsewhere in the UI not reached, or may be a real, minor product-completeness gap; not confirmed either way this session |
| 7 | `"Couldn't reach the server"` message shown for a `LICENSE_INACTIVE` denial | Low, cosmetic | Implies a network issue rather than the true cause; the underlying security property (mutation genuinely denied) is correct regardless |
| 8 | Android background-process freezing (Android 12+ freezer) requires foregrounding an app before its embedded local API responds | Not a defect | Real platform behavior, documented here as an operational note for future device sessions, not a product bug |
| 9 | Mid-session real USB disconnect/reconnect | Not a defect | Real hardware event, real recovery performed, documented in `phase8vp4-baseline.md` |

## No P0, no P1 found this session

Item 5 is the most concrete new finding -- assessed low-medium, a real usability gap rather than a
security or data-integrity defect (the authoritative Owner-side record is correct throughout; only
the *device's own UI* lacks a direct path back to reactivation after an explicit local deactivation).
Not fixed this session, consistent with this phase's own validation-only scope.

## Phase 8V-P5 additions (additive, this register's prior entries unchanged)

| # | Item | Severity | Status |
|---|---|---|---|
| 10 | `emergency_extensions.py` defaulted `now` to naive `datetime.utcnow()` instead of the shared tz-aware `utcnow()`, shifting stored extension windows by the DB session's UTC offset (3h on this dev Postgres) | **P1** | **Fixed this session** -- all 3 call sites corrected, regression test added, 12/12 + 16/16 collateral files pass |
| 11 | `EmergencyExtension` (business/audit layer) and `OfflinePolicy.emergency_extension_allowed/until` (technical grace-extension layer) are never wired together -- creating a real, approved, audited extension has no functional effect on a device's local grace/restriction computation, only an informational `emergency_extension_id` in the assertion | **P1, disclosed, not fixed** | Found via careful inspection of real captured wire evidence (`offline_policy.emergency_extension_until: null` despite an active extension); confirmed via source (`policy_evaluator.py:108` reads only the `OfflinePolicy` fields). See `docs/owner/phase8vp5/scenario5-emergency-extension-and-expiry-final.md` |
| 12 | Item 1 (Scenario 6/7) and item 2 (2/3/5 sub-checks) from this table remain open after Phase 8V-P5 -- Scenario 2's restricted-to-active/88.00/returns and Scenarios 6/7 were not reached this session either; real RESTRICTED itself *was* finally achieved (Clinic, via a dedicated OfflinePolicy), closing part of item 2 | Blocks final tag | See `docs/owner/phase8vp5/phase8-final-decision.md` for the full updated per-dimension verdict |
| 13 | Item 5 (deactivation UX) formally classified this session: intent is confirmed "fresh activation via key required" (case A), and the missing key-entry form on `DEVICE_DEACTIVATED` is confirmed a real P2 gap (state machine supports `reactivation_requested -> ACTIVATION_REQUIRED`, screen never triggers it) | P2 (reclassified from unclassified) | Not fixed, per the spec's own instruction for case A. See `docs/owner/phase8vp5/local-deactivation-reactivation-decision.md` |

## Phase 8V-P6 additions (additive; resolves items 11 and part of 12 above)

| # | Item | Severity | Status |
|---|---|---|---|
| 14 | Item 11 above (EmergencyExtension/OfflinePolicy wiring gap) | P1 | **RESOLVED** -- `serialize_policy_for_subscription()` (per-request override, never mutates the shared `OfflinePolicy` row), physically proven: real extension moved a real device Restricted -> Active, a real domain-data write succeeded during the window, and the device automatically reverted on real elapsed expiry. See `docs/owner/phase8vp6/scenario5-emergency-extension-functional-final.md` |
| 15 | A second, related P1, found and fixed this session, not previously named as its own item: `subscription_status` was already signed into every assertion and already parsed client-side but never consulted by `policy_evaluator.py::evaluate()`; separately, `license_status == "SUSPENDED"` was missing from the hard-state list entirely (root cause of the real SUSPENDED-license sale from Phase 8V-P5) | P1 | **RESOLVED** -- both wired in, additive, no contract version bump, 14 new automated tests, physically proven for the subscription-EXPIRED case (license-SUSPENDED path covered by automated tests only, not separately re-run physically this session). See `docs/owner/phase8vp6/scenario3-commercial-enforcement-final.md` |
| 16 | Item 12's remaining half (Scenario 6/7, and Scenario 2's Retail-side restricted-to-active/88.00/returns) | Blocks final tag | Still open after Phase 8V-P6 -- Retail Android was not rebuilt this session. See `docs/owner/phase8vp6/phase8vp6-final-decision.md` |
| 17 | Stale-assertion rejection, backup/restore/export, Clinic invoice/payment integrity | Blocks final tag | Still open after Phase 8V-P6, same disclosed reasons as Phase 8V-P5 (time budget) |
