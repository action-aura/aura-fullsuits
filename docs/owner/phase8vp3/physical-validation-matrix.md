# Phase 8V-P3 — Physical Validation Matrix

Planned coverage vs. actual outcome. No device connected this session -- every row's actual column is
NOT VERIFIED for the identical, single, disclosed reason.

| # | Scenario / Check | Product | Planned validation | Actual result |
|---|---|---|---|---|
| 1 | Early renewal | Clinic | Physical check-in, assertion refresh, persistence | NOT VERIFIED -- no device |
| 2 | Late renewal / revival | Retail | Physical check-in, RESTRICTED -> ACTIVE_ONLINE, 88.00 case | NOT VERIFIED -- no device |
| 3 | Past due -> restricted | Retail | Physical check-in, grace/warning UX, backend enforcement | NOT VERIFIED -- no device |
| 4 | Pilot conversion | Clinic | Physical check-in, no patient data in traffic | NOT VERIFIED -- no device |
| 5 | Emergency extension | Clinic | Physical check-in, expiry, negative tests | NOT VERIFIED -- no device |
| 6 | Device replacement | Retail | Physical second-device activation flow | NOT VERIFIED -- no device |
| 7 | Plan downgrade / overage | Retail | Physical confirmation of the already-fixed Owner-side sync | NOT VERIFIED -- no device |
| -- | Installation ID / device-key / slot continuity | Both | Cross-scenario invariant | NOT VERIFIED -- no device |
| -- | Assertion state-version monotonicity + stale rejection | Both | Cross-scenario invariant | NOT VERIFIED -- no device |
| -- | Restart / force-stop / process-kill persistence | Both | Part O | NOT VERIFIED -- no device |
| -- | Direct backend enforcement in restricted states | Both | Part P | NOT VERIFIED -- no device |
| -- | Real traffic capture + data-boundary review | Both | Part Q | NOT VERIFIED -- no device |
| -- | Logcat privacy review | Both | Part R | NOT VERIFIED -- no device |
| -- | On-device data preservation | Both | Part S | NOT VERIFIED -- no device |

Every row above was proven correct at the Owner/service tier (all seven scenarios) and, for six of
seven, at the real-installed-Windows-product tier (Phase 8V-P) -- see
`docs/owner/phase8vp2/android-physical-gate-matrix.md` for that accounting, unchanged since. This
matrix concerns only the Android-physical-hardware column, which remains open for the same single
reason across three consecutive sessions (Phase 8V-P, 8V-P2, 8V-P3): no device has been connected in
this environment.
