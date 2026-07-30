# Phase 8V-P3 — Final Physical Validation Matrix

Identical in substance to `physical-validation-matrix.md` (written at session start as a plan) --
reproduced here as the Part V-named closing artifact per this phase's own documentation checklist,
since the outcome did not change: no device connected, so nothing physical was executed.

| Row | Result |
|---|---|
| Physical device readiness | NOT READY -- empty `adb devices -l` |
| Clinic signed installation/upgrade | NOT VERIFIED -- no device |
| Retail signed installation/upgrade | NOT VERIFIED -- no device |
| Scenario 1 (Clinic early renewal) | NOT VERIFIED -- no device |
| Scenario 2 (Retail late renewal) | NOT VERIFIED -- no device |
| Scenario 3 (Retail past due) | NOT VERIFIED -- no device |
| Scenario 4 (Clinic pilot conversion) | NOT VERIFIED -- no device |
| Scenario 5 (Clinic emergency extension) | NOT VERIFIED -- no device |
| Scenario 6 (Retail device replacement) | NOT VERIFIED -- no device |
| Scenario 7 (Retail plan downgrade) | NOT VERIFIED on-device; **Owner-side defect already fixed and PASS** (Phase 8V-P2) |
| Identity/slot continuity (physical) | NOT VERIFIED -- no device |
| Assertion refresh / monotonicity / stale rejection (physical) | NOT VERIFIED -- no device |
| Restart/force-stop/kill persistence (physical) | NOT VERIFIED -- no device |
| Backend enforcement (physical, on-device direct request) | NOT VERIFIED -- no device |
| Traffic privacy (physical capture) | NOT VERIFIED -- no device |
| Logcat privacy (physical capture) | NOT VERIFIED -- no device |
| Data preservation (physical) | NOT VERIFIED -- no device |
| Owner automated regression | **PASS** -- 394/394 |
| commercial_runtime automated regression | **PASS** -- 219/219 |
| Certificate continuity | **PASS** -- reconfirmed, full SHA-256 match, both products |
| Environment preflight | **PASS** -- `ok: true`, reconfirmed live |

Total physical scenarios completed this session: **0 of 7** (three consecutive sessions now: Phase
8V-P, 8V-P2, 8V-P3, all blocked on the identical single cause).
