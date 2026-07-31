# Phase 8V-P7 — Final Physical Gate Matrix (closing snapshot)

Same content as `remaining-physical-gate-matrix.md` (the pre-session matrix, updated live as work
completed) -- reproduced here as the explicit closing snapshot the governing spec's Part X names
separately, so the "before" and "after" views both exist as distinct artifacts rather than one file
silently standing in for both.

| Gate | Final status |
|---|---|
| Artifact alignment (all 4 families, rc.4) | PASS |
| Windows build-impact resolution | PASS (corrected Phase 8V-P6's own conclusion) |
| Scenario 1 (retained) | PASS (not re-smoked this session; no regression risk) |
| Scenario 2 (Retail late renewal) | PARTIAL (restriction proven; renewal-restoration/88.00/returns blocked by device disconnection) |
| Scenario 3 | PASS (physically re-proven on rebuilt artifact) |
| Scenario 4 (retained) | PASS (not re-smoked this session; no regression risk) |
| Scenario 5 (retained) | PASS (Phase 8V-P6 evidence stands; not re-smoked this session) |
| Scenario 6 (device replacement) | PASS (real, corrected from earlier pessimistic constraint disclosure) |
| Scenario 7 (plan downgrade/overage) | PASS for Owner-side mechanics; device-facing confirmation NOT VERIFIED |
| Stale-assertion rejection | NOT VERIFIED |
| Clinic backup/restore/export | NOT VERIFIED |
| Retail backup/restore/export | NOT VERIFIED |
| Clinic invoice/payment | NOT VERIFIED |
| Retail 88.00 | NOT VERIFIED |
| Retail returns | NOT VERIFIED |
| Backend enforcement | PASS (real, both products, this session) |
| Raw wire privacy | PARTIAL (clean, limited scope) |
| Logcat privacy | PARTIAL (real spot-checks clean, not systematic) |
| On-device data preservation | PARTIAL (real positive evidence, no full baseline) |
| Automated regression | PASS (960/960, plus 329/329 re-confirmation after version bump) |
| Artifact/manifest audit | PASS |
| **Phase 8 overall** | **CONDITIONAL PASS continues; final tag withheld** |
