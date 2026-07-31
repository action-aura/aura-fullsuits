# Phase 8V-P3 — Final Phase 8 Decision

## Per-dimension verdicts

| # | Dimension | Verdict |
|---|---|---|
| 1 | Physical device readiness | **NOT READY** -- empty `adb devices -l` |
| 2 | Clinic signed installation/upgrade | **NOT VERIFIED** -- no device |
| 3 | Retail signed installation/upgrade | **NOT VERIFIED** -- no device |
| 4 | Scenario 1 (Clinic early renewal) | **NOT VERIFIED** -- no device (Owner-side: PASS, Phase 8V-P) |
| 5 | Scenario 2 (Retail late renewal) | **NOT VERIFIED** -- no device (Owner-side: PASS, Phase 8V-P) |
| 6 | Scenario 3 (Retail past due) | **NOT VERIFIED** -- no device (Owner-side: PASS, Phase 8V-P) |
| 7 | Scenario 4 (Clinic pilot conversion) | **NOT VERIFIED** -- no device (Owner-side: PASS, Phase 8V-P) |
| 8 | Scenario 5 (Clinic emergency extension) | **NOT VERIFIED** -- no device (Owner-side: PASS, Phase 8V-P) |
| 9 | Scenario 6 (Retail device replacement) | **NOT VERIFIED** -- no device (Owner-side: PASS, Phase 8V-P) |
| 10 | Scenario 7 (Retail plan downgrade) | **NOT VERIFIED** -- no device (Owner-side: **PASS**, Phase 8V-P2 -- the defect is fixed) |
| 11 | Renewal without license-key retransmission | **PASS at Owner/service tier**; NOT VERIFIED physically |
| 12 | Installation continuity | **PASS at Owner/service tier**; NOT VERIFIED physically |
| 13 | Device-key continuity | **PASS at Owner/service tier**; NOT VERIFIED physically |
| 14 | Device-slot continuity | **PASS at Owner/service tier**; NOT VERIFIED physically |
| 15 | Assertion refresh | **PASS at Owner/service tier**; NOT VERIFIED physically |
| 16 | Monotonic assertion state | **PASS at Owner/service tier**; NOT VERIFIED physically |
| 17 | Stale-assertion rejection | **PASS**, unchanged, pre-existing Phase 6/7 behavior; NOT VERIFIED physically this session |
| 18 | Restricted-to-active restoration | **PASS**, unchanged (Phase 8V-P); NOT VERIFIED physically this session |
| 19 | Backend enforcement | **PASS at capability-guard-test tier**; NOT VERIFIED physically |
| 20 | Traffic privacy | **NOT VERIFIED** -- no device, nothing captured |
| 21 | Logcat privacy | **NOT VERIFIED** -- no device |
| 22 | Product data preservation | **NOT VERIFIED** physically; structurally guaranteed unchanged (no product code touched) |
| 23 | Backup/restore/export preservation | **PASS**, unchanged, pre-existing; NOT VERIFIED physically this session |
| 24 | Phase 8 overall | **CONDITIONAL PASS** -- unchanged qualifier, same single remaining gate as Phase 8V-P and 8V-P2 |

## Decision

**No new tag created.** Every mandatory PASS condition this phase's own Part U lists that depends on
a physical device is unmet, for the identical, single, disclosed reason across three consecutive
sessions: no Android device has been connected in this environment. The existing
`aura-owner-commercial-ops-phase8-conditional-complete` tag is unmoved.

## What genuinely changed since Phase 8V-P2

- A new, real, previously-undocumented precondition was found: the current rc.3 artifacts have an
  empty (by-design) licensing URL and must be rebuilt with `-PownerLicensingBaseUrl=...` before a
  physical licensing session can produce meaningful results. This is now a solved problem in
  documentation (`artifact-verification.md` has the exact command) rather than something the next
  session has to discover mid-validation.
- Owner and commercial_runtime automated suites re-confirmed at the exact same HEAD (394/394,
  219/219) -- no drift across the gap between sessions.
- Environment preflight re-confirmed `ok: true` live against the real dev database.

Nothing about the Android-physical gap itself narrowed -- it is exactly as open as it was, because it
depends on hardware, not software.

## Phase 8V-P4 update (additive)

A physical device connected in Phase 8V-P4 -- the gap this document describes started closing for
real. See `docs/owner/phase8vp4/phase8-final-unconditional-decision.md` for the full accounting.
Still CONDITIONAL PASS; still no final tag; several scenarios remain partially or fully unverified
from that device.

## Recommended next session's exact scope

Connect a physical Android device. Confirm `adb devices -l` shows `device`. Rebuild both products
with the `-PownerLicensingBaseUrl` command in `artifact-verification.md` (using `adb reverse` to keep
the connection strictly local). Install, run the seven scenarios, capture traffic and Logcat per the
two plan documents already written this session, write the closing decision, and only then create the
final tag if every gate genuinely passes.

## Later update (Phase 8V-P5, additive)

This chain continued through Phase 8V-P4 and Phase 8V-P5. As of Phase 8V-P5: real physical RESTRICTED
state was finally achieved (Phase 8V-P/P2/P3/P4 never reached it), a real P1 timezone bug in the
emergency-extension service was found and fixed, and the full automated regression (943 tests including
product backends) passed. Scenarios 2/6/7, stale-assertion rejection, and backup/restore/export remain
open. Still CONDITIONAL PASS; still no final tag. See
`docs/owner/phase8vp5/phase8-final-decision.md` for the current, authoritative status.
