# Phase 7V-F — Remaining Gate Matrix (Part A)

Gap list this phase set out to close, and final status.

| # | Gap (from Phase 7V-F spec) | Status |
|---|---|---|
| 1 | Physical Android signed rc.1→rc.2 upgrade, Clinic | **DONE** — real, live |
| 2 | Physical Android signed rc.1→rc.2 upgrade, Retail | **NOT DONE** — device disconnected |
| 3 | Physical Android activation lifecycle, both products | Clinic: partial (data/upgrade proven, activation build prepared, on-device activation not completed); Retail: not attempted |
| 4 | Physical Android online check-in, both products | NOT DONE (physical) — proven live on Windows (shared code) |
| 5 | Physical Android restart/force-stop/process-kill persistence | Clinic: DONE for rc.1 pre-upgrade and post-upgrade data; Retail: not attempted |
| 6 | Physical Android offline continuity | NOT DONE (physical) — proven live on Windows |
| 7 | Physical Android warning/grace/restricted | NOT DONE (physical) — proven live on Windows, **including finding and fixing the real P0 that made this untestable at all until fixed** |
| 8 | Physical Android suspend/reactivate/deactivate | NOT DONE (physical) — proven live via direct Owner-admin calls + Windows |
| 9 | Physical Android Kotlin/embedded-Python authority | NOT DONE (physical) — unchanged from Phase 7V's source/protocol-level proof |
| 10 | Physical Android Logcat privacy | NOT DONE (physical) — equivalent traffic content inspected live on Windows |
| 11 | Retail Windows rc.1→rc.2 upgrade with existing data | **DONE** — real, live, including the 88.00 case |
| 12 | Live Windows restricted-mode enforcement | **DONE** — real, live, both products, post-fix |
| 13 | Production trust-anchor generation + artifact alignment | **DONE** — real signing key, real trust anchor, bundled into all 4 rebuilt artifacts |
| 14 | Final evidence, decision, closing tag | Evidence and decision: DONE. Closing tag: **WITHHELD** — gaps #2–#10 remain |

## Net assessment

This session achieved substantially more real verification than Phase 7V left off with — including
finding and fixing a genuine P0 defect in the core offline-enforcement mechanism that no prior
testing (unit or live) had caught — but did not achieve full physical Android completion for both
products, because the device disconnected unreliably partway through and did not stay connected
for the remainder of the session despite repeated genuine reconnection checks.
