# Phase 8V-P4 — Final Physical Gate Matrix

| # | Gate | Result |
|---|---|---|
| 1 | Physical device readiness | **PASS** -- Infinix X6528, stable, real (one real mid-session USB disconnect, real recovery, documented) |
| 2 | URL-configured rebuild | **PASS** -- both products, source-verified path semantics, real reachable endpoint |
| 3 | Clinic APK/AAB | **PASS** -- signed, non-debuggable, cert matches historical identity |
| 4 | Retail APK/AAB | **PASS** -- same |
| 5 | Certificate continuity | **PASS** -- full SHA-256 match both products |
| 6 | Signed installation/upgrade | **PASS** -- real rc.2->rc.3 in-place upgrade, both products |
| 7 | Initial activation | **PASS** -- real Ed25519 activation, both products, cross-verified on Owner |
| 8 | Scenario 1 (Clinic early renewal) | **PASS** -- fully real |
| 9 | Scenario 2 (Retail late renewal) | **CONDITIONAL** -- renewal mechanics real; RESTRICTED state and 88.00 case not directly observed |
| 10 | Scenario 3 (past due) | **CONDITIONAL** -- Owner-side resolution real and correct; RESTRICTED end-state not reached |
| 11 | Scenario 4 (Clinic pilot conversion) | **PASS** -- fully real |
| 12 | Scenario 5 (emergency extension) | **CONDITIONAL** -- creation and propagation real; distinguishing effect not isolated |
| 13 | Scenario 6 (device replacement) | **NOT VERIFIED this session** -- only one physical device available |
| 14 | Scenario 7 (plan downgrade) | **NOT VERIFIED this session** -- same reason |
| 15 | Renewal without license key | **PASS** -- confirmed across every scenario run |
| 16 | Installation-ID continuity | **PASS** -- confirmed across every scenario run |
| 17 | Device-key continuity | **PASS** -- no re-registration observed at any point |
| 18 | Device-slot continuity | **PASS at the tested tier** -- no new slot consumed by any renewal/conversion this session |
| 19 | Assertion refresh | **PASS** -- new `assertion_expires_at` on every successful check-in |
| 20 | State-version monotonicity | **PASS structurally** -- not independently stress-tested this session beyond normal operation |
| 21 | Stale-assertion rejection | **NOT independently stress-tested this session** -- unchanged source, covered by the green Owner suite |
| 22 | Restricted-to-active restoration | **PASS at the subscription-status tier** (EXPIRED->ACTIVE observed for real); local RESTRICTED->ACTIVE_ONLINE visual transition not observed |
| 23 | Backend enforcement | **PASS for the one real case tested** (LICENSE_INACTIVE, pre-activation) |
| 24 | Traffic privacy | **PASS for what was captured** (local status API + Logcat); raw wire capture not performed |
| 25 | Logcat privacy | **PASS** -- zero secret/customer-data leakage across every real operation this session |
| 26 | Data preservation | **PASS** for every data point captured |
| 27 | Backup/restore/export | **NOT exercised this session** |

## Physical scenarios completed this session: **2 of 7 fully real** (Scenarios 1, 4), **3 of 7
conditionally real** (Scenarios 2, 3, 5 -- core mechanics proven, one or more sub-checks not
reached), **2 of 7 not independently verified this session** (Scenarios 6, 7 -- single-device
constraint, mechanics already proven in prior sessions).
