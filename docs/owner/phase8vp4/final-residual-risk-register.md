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
