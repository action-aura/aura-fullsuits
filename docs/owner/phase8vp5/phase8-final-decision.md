# Phase 8V-P5 — Final Phase 8 Decision

## Overall verdict: **CONDITIONAL PASS continues — final unconditional tag WITHHELD**

The existing tag `aura-owner-commercial-ops-phase8-conditional-complete` remains the accurate marker of
project state. `aura-commercial-licensing-operations-phase8-complete` is **not created** this session.
This is the same honest pattern every prior Phase 8 session has followed, and this session is no
exception, despite genuine, substantial forward progress (see below).

## Real progress made this session (not to be understated)

1. Root-caused, via direct source reading across `checkin.py`, `expiry_scan.py`, `policy_evaluator.py`,
   `capability_guard.py`, and `offline_policy.py`, exactly why local `RESTRICTED` state had never been
   observed across four prior sessions -- a genuine architectural finding, not a guess.
2. Achieved real, physical, on-device `RESTRICTED` state for the first time across all sessions, via the
   correct mechanism (a dedicated non-default `OfflinePolicy` + real elapsed offline time), and proved
   real backend enforcement (a denied `Save Patient` write) during it.
3. Found, fixed, and regression-tested a real P1 bug: `emergency_extensions.py` used naive
   `datetime.utcnow()` instead of the codebase's shared timezone-aware helper, silently shifting stored
   extension windows by the DB session's UTC offset (3 hours on this project's real dev Postgres).
4. Found and disclosed a second, more significant structural gap: the business-level `EmergencyExtension`
   record and the technical `OfflinePolicy.emergency_extension_until` grace-extension field are never
   wired together -- a real, approved, audited emergency extension currently has **no functional effect**
   on a device's local grace computation, only an informational one. This is a real product-completeness
   finding, caught by careful inspection of real captured wire evidence rather than assumed from a
   coincidental state change.
5. Ran the complete automated regression for real, **explicitly including product backends this time**
   (943 tests across Owner/commercial_runtime/licensing_contracts/Retail/Clinic, 0 failures) -- correcting
   the exact gap the governing spec called out by name from prior sessions.
6. Classified the local-deactivation reactivation question definitively from source (case A confirmed;
   real minor P2 UI gap disclosed, no production code changed per the spec's own instruction for case A).
7. Captured, redacted, and documented real (if limited) wire traffic and confirmed a clean data boundary
   for what was captured.

## Per-dimension verdict (governing spec Part T's 31 dimensions)

| # | Dimension | Verdict |
|---|---|---|
| 1 | Device readiness | PASS (real, reconfirmed) |
| 2 | Owner preflight | PASS (real, reconfirmed) |
| 3 | Connectivity | PASS (real, reconfirmed, tunnel drops handled per established protocol) |
| 4 | Scenario 1 (retained) | PASS (retained from Phase 8V-P4, no source change) |
| 5 | Scenario 2 final | **NOT VERIFIED** (SUSPEND proven; restricted-to-active transition, 88.00, returns not proven for Retail) |
| 6 | Scenario 3 final | PARTIAL (Owner-tier mechanics + structural finding proven; literal past-due UX walk not separately proven) |
| 7 | Scenario 4 (retained) | PASS (retained from Phase 8V-P4, no source change) |
| 8 | Scenario 5 final incl. expiry | PARTIAL (RESTRICTED + business layer proven; technical isolated-effect/expiry not provable -- mechanism unwired) |
| 9 | Scenario 6 | **NOT VERIFIED** (not attempted this session) |
| 10 | Scenario 7 | **NOT VERIFIED** (not attempted this session) |
| 11 | Emergency-extension expiry | **NOT VERIFIED** (see #8) |
| 12 | Stale-assertion rejection | **NOT VERIFIED** (not attempted this session) |
| 13 | Renewal-without-key | Not separately re-proven (no key retransmission observed in any captured exchange -- consistent, not independently exercised via a renewal this session) |
| 14 | Installation/device-key/slot continuity | PASS (stable across all captured exchanges) |
| 15 | Assertion refresh | PASS (fresh assertion on every captured check-in) |
| 16 | State-version monotonicity | Not independently exercised (no scenario produced a state-version-changing sequence to observe monotonicity across, beyond the single emergency-extension assertion) |
| 17 | Restricted-to-active restoration | **NOT VERIFIED** (RESTRICTED reached; return to ACTIVE via a real signed transition not isolated from ordinary check-in reset, see Scenario 5) |
| 18 | Backend enforcement | PASS (real, physical, both a UI denial and no partial write confirmed during real RESTRICTED) |
| 19 | Retail 88.00 integrity | **NOT VERIFIED** |
| 20 | Retail return integrity | **NOT VERIFIED** |
| 21 | Clinic invoice/payment integrity | **NOT VERIFIED** |
| 22 | Backup | **NOT VERIFIED** |
| 23 | Restore | **NOT VERIFIED** |
| 24 | Export | **NOT VERIFIED** |
| 25 | Raw wire privacy | PARTIAL (clean for the 7 exchanges captured; most scenario traffic never generated) |
| 26 | Logcat privacy | PARTIAL (real spot-check clean; full per-scenario protocol not run) |
| 27 | Product data preservation | **NOT VERIFIED** (no baseline created to diff against) |
| 28 | Final automated regression | **PASS** (943/943, product backends included) |
| 29 | Zero P0 | PASS (none found) |
| 30 | Zero P1 | **One found, and fixed, this session** (timezone bug) -- zero P1 *remaining* as of HEAD; the wiring gap (#8) is assessed P1-class but explicitly deferred, not silently left as zero |
| 31 | Phase 8 overall | **CONDITIONAL PASS continues; tag withheld** |

## Explicit reasons the final tag is withheld (naming the governing spec's own gate list)

- Scenarios 2, 6, 7 incomplete.
- Emergency-extension isolated effect and expiry not provable (mechanism unwired, disclosed as a real
  finding rather than worked around).
- Stale assertion not independently rejected this session.
- Backup/restore/export not tested.
- Retail 88.00/returns not tested.
- Clinic invoice/payment not tested.
- Restricted-to-active restoration not isolated from ordinary check-in reset.

None of the disqualifying conditions relating to *safety* were triggered (no key retransmission
observed, no identity churn, no renewal consuming an extra slot, no forbidden data in captured traffic
or Logcat, no P0, Git tree intended to remain clean after this session's commit, no Phase 9 work
performed) -- the gaps are entirely about *incomplete scenario coverage*, not regressions or safety
issues.

## Recommendation for the next session

Prioritize, in order: (1) reproduce Scenario 5's RESTRICTED mechanism on the Retail installation and
complete the late-renewal + 88.00 + return walk (Scenario 2); (2) decide whether to implement the
emergency-extension wiring fix (connecting `EmergencyExtension` to `OfflinePolicy.emergency_extension_until`)
before or after re-attempting Scenario 5's isolated-effect proof; (3) start the real Windows Retail
install to unlock Scenarios 6 and 7; (4) backup/restore/export and Clinic invoice/payment, which are
UI-only and comparatively fast; (5) stale-assertion, which needs the capture middleware extended with a
hold/release capability.
