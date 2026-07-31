# Phase 8V-P7 — Remaining Physical Gate Matrix (final status)

| # | Gate | Status |
|---|---|---|
| 1 | Final cross-platform artifact alignment | **DONE** -- all 4 families rebuilt, rc.4 |
| 2 | Retail Android rebuild | **DONE** |
| 3 | Windows embed/build-impact resolution | **DONE** -- both Windows exes rebuilt, real finding corrected Phase 8V-P6's "not required" conclusion |
| 4 | Scenario 2 — Retail late renewal | **PARTIAL** -- restriction proven physically; renewal restoration/88.00/returns blocked by device disconnection |
| 5 | Retail physical RESTRICTED -> ACTIVE_ONLINE | **PARTIAL** -- RESTRICTED half proven; ACTIVE_ONLINE restoration not confirmed on-device |
| 6 | Retail 88.00 calculation | **NOT VERIFIED** |
| 7 | Retail return integrity | **NOT VERIFIED** |
| 8 | Scenario 6 — device replacement | **DONE** -- real, PASS, via genuine multi-instance Windows identities |
| 9 | Scenario 7 — plan downgrade/overage | **DONE** for Owner-side mechanics; device-facing confirmation not verified |
| 10 | Stale-assertion rejection | **NOT VERIFIED** |
| 11 | Clinic backup/restore/export | **NOT VERIFIED** |
| 12 | Retail backup/restore/export | **NOT VERIFIED** |
| 13 | Clinic invoice/payment integrity | **NOT VERIFIED** |
| 14 | Full data-preservation comparison | **PARTIAL** -- real positive evidence, no full baseline |
| 15 | Raw wire capture | **PARTIAL** -- real, clean, limited scope |
| 16 | Logcat privacy review | **PARTIAL** -- real spot-checks clean, not systematic |
| 17 | Full final regression | **DONE** -- 960/960 before Android work, re-confirmed for product backends after version bump |
| 18 | Final manifests/hashes/signing | **DONE** |

## Root cause of the partial items

An extended, real, disclosed physical Android device disconnection partway through this session (see
`phase8vp7-baseline.md`), after Scenario 2's restriction half and Scenario 3's smoke check were already
completed on the rebuilt artifact, and before the renewal-confirmation/88.00/returns/backup-restore-
export/invoice-payment/stale-assertion work could be reached. Multiple real recovery attempts
(`adb kill-server`/`start-server`, waiting, rechecking) did not restore the connection within this
session's remaining time.
