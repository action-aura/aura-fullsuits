# Phase 8V-P7 — Final Residual Risk Register

## Carried forward, unchanged

| # | Item | Severity | Status |
|---|---|---|---|
| 1 | `DEVICE_DEACTIVATED` has no reachable in-app key-entry route | P2 | Unfixed, disclosed, does not block (see `local-deactivation-recovery-final.md`) |
| 2 | Windows executables have no Authenticode signing | Informational | Pre-existing project condition, not a regression, not previously named as a Phase 8 gate |

## New this session

| # | Item | Severity | Status |
|---|---|---|---|
| 3 | `dist/AuraRetail.exe` / `dist/AuraClinic.exe` predated the Phase 8V-P6 fix by 4 real days -- a real, confirmed staleness gap in the artifact pipeline that Phase 8V-P6 itself did not catch (that session concluded "Windows rebuild not required" without checking Windows timestamps against the fix commit) | Found and fixed this session | **RESOLVED** -- both rebuilt, confirmed via PyInstaller's own change-detection log line |
| 4 | `scan_over_limit_licenses()` evaluates temporary exceptions at date-precision (midnight), not real current time -- a same-day, short-duration exception is invisible to the scan's own finding logic even though it is fully effective for the real activation-time check | Low, disclosed, non-blocking design characteristic | Not fixed (consistent with `expiry_scan.py`'s own documented daily-batch convention); recorded for awareness, not treated as a defect requiring a fix |
| 5 | Extended physical Android device disconnection blocked Scenario 2 completion, stale-assertion, both products' backup/restore/export, and Clinic invoice/payment | Session-limiting, not a code defect | Real, disclosed, multiple real recovery attempts made; carried to next session's own entry-gate priority |

## Zero P0, zero P1 remaining

Both real P1s from Phase 8V-P6 (subscription-status enforcement, emergency-extension wiring) remain
fixed and, this session, additionally re-confirmed to survive an artifact rebuild cycle on both
products. No new P0/P1 was found this session -- item 3 above was found and fixed within this same
session, not left open.
