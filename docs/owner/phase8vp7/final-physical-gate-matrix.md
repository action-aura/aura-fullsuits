# Phase 8V-P7 — Final Physical Gate Matrix (closing snapshot, post-reconnection)

Supersedes the earlier mid-session snapshot in `remaining-physical-gate-matrix.md` -- the device
reconnected later in this session and real further work closed several of that snapshot's open items.

| Gate | Final status |
|---|---|
| Artifact alignment (all 4 families, rc.4) | PASS |
| Windows build-impact resolution | PASS |
| Scenario 1 (retained) | PASS |
| Scenario 2 -- restriction, renewal restoration, return integrity | PASS |
| Scenario 2 -- 88.00 calculation | NOT VERIFIED (real, disclosed UI gap) |
| Scenario 3 | PASS |
| Scenario 4 (retained) | PASS |
| Scenario 5 (retained) | PASS |
| Scenario 6 (device replacement) | PASS |
| Scenario 7 -- Owner-side mechanics | PASS |
| Scenario 7 -- device-facing confirmation | NOT VERIFIED |
| Stale-assertion rejection | NOT VERIFIED |
| Clinic backup/restore | PASS |
| Retail backup/restore | PASS |
| Export (distinct feature) | NOT VERIFIED (not located) |
| Clinic invoice/payment | PASS |
| Backend enforcement | PASS (real, both products) |
| Raw wire privacy | PASS (clean, limited scope) |
| Logcat privacy | PASS (clean, limited scope) |
| On-device data preservation | PASS (real, limited scope) |
| Automated regression | PASS (960/960 + 329/329) |
| Artifact/manifest audit | PASS |
| **Phase 8 overall** | **CONDITIONAL PASS continues; final tag withheld** |

## Remaining gap, precisely

Three items: Retail 88.00 calculation, stale-assertion rejection, Scenario 7's device-facing
confirmation. All three have a real, disclosed, non-defect reason for being incomplete this session
(no UI field + no session credential; no proxy hold/release capability built; Windows instances already
stopped + license-identity conflict with the physical device). Everything else in the governing spec's
own mandatory-gate list for the final tag is now real and PASS.
