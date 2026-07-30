# Phase 8V-P2 — Final Phase 8 Release Decision

## Per-dimension verdicts

| # | Dimension | Verdict |
|---|---|---|
| 1 | Scenario 7 resolution | **PASS** (real defect found, fixed, 8 automated tests + live-DB re-verification -- `scenario7-final-evidence.md`) |
| 2 | Environment preflight | **PASS** (new `flask commercial preflight` command, 8 tests, found + fixed a third real environment gap for real -- `environment-preflight-report.md`) |
| 3 | Owner automated validation | **PASS** (394/394) |
| 4 | commercial_runtime validation | **PASS** (219/219) |
| 5 | Product backend validation | **PASS, unchanged** (not re-run -- no product backend code changed this session; last validated in Phase 8V-P) |
| 6 | Clinic Android rc.3 build | **PASS** (signed APK + AAB built, tests + lint pass) |
| 7 | Retail Android rc.3 build | **PASS** (signed APK + AAB built, tests + lint pass) |
| 8 | Clinic certificate continuity | **PASS** (full SHA-256 digest matches historical identity) |
| 9 | Retail certificate continuity | **PASS** (full SHA-256 digest matches historical identity) |
| 10 | Clinic Android physical lifecycle | **NOT VERIFIED** -- no device |
| 11 | Retail Android physical lifecycle | **NOT VERIFIED** -- no device |
| 12 | Renewal without key retransmission | **PASS** (unchanged code path; Scenario 7 fix touches only `Subscription`/`License`/`RenewalRequest`/`AuditLog` rows, never a license key) |
| 13 | Identity continuity (installation/device-key) | **PASS at Owner tier** (Scenario 7 tests explicitly assert pre-existing installations are untouched); physical confirmation NOT VERIFIED |
| 14 | Device-slot continuity | **PASS at Owner tier**, same caveat |
| 15 | Restricted-to-active restoration | **PASS, unchanged** (Phase 8V-P, not touched this session) |
| 16 | Android traffic privacy | **NOT VERIFIED** -- no device, no traffic captured |
| 17 | Android Logcat privacy | **NOT VERIFIED** -- no device |
| 18 | Android data preservation | **NOT VERIFIED** -- no device |
| 19 | Phase 8 overall | **CONDITIONAL PASS** -- narrower gap than Phase 8V-P, see below |

## Why this is still not an unconditional PASS

Every gate this phase's own governing brief lists as blocking the final tag that depends on a
physical Android device is still open, for one single, disclosed, hardware-availability reason: no
device connected this session (`physical-android-readiness.md`). Per the brief's own rule, the final
`aura-commercial-licensing-operations-phase8-complete` tag is **not created**.

## What genuinely changed since Phase 8V-P's own CONDITIONAL PASS

Phase 8V-P's gap was two-part: "Scenario 7 conditional on a disclosed feature gap" AND "no physical
Android device access." This session closed the first part completely:

- Scenario 7 is now unconditional **PASS** at the Owner/service tier -- the real defect is fixed,
  not merely worked around, with real automated and live-database evidence.
- A new, genuinely useful safeguard (`flask commercial preflight`) now exists and, in using it for
  real, found and fixed a third real environment defect that would otherwise have kept lurking.
- Both products' final signed rc.3 Android artifacts now exist, are built from the exact code that
  will ship, and have verified certificate continuity -- meaning the *only* thing a device session
  needs to do next is install and click through, not also stop to build or debug a signing issue.

The second part -- physical Android device access -- remains exactly as open as it was, because it
depends on hardware this environment does not have, not on anything resolvable in software.

## Phase 8V-P3 update (additive -- this section only, verdicts above unchanged)

No device connected in Phase 8V-P3 either (third consecutive session). One real, previously
undocumented precondition was found: the rc.3 artifacts referenced above have an empty (by-design)
`OWNER_LICENSING_BASE_URL` compiled in and need a one-command rebuild with
`-PownerLicensingBaseUrl=...` before a real licensing device session -- see
`docs/owner/phase8vp3/artifact-verification.md` for the exact command. Owner and commercial_runtime
suites reconfirmed at the same HEAD with no drift. See
`docs/owner/phase8vp3/phase8-final-decision.md`. Still no final tag.

## Phase 8V-P4 update (additive)

A physical device finally connected. Real signed rc.3 rebuild, real physical activation (both
products), real early-renewal and pilot-conversion scenarios fully proven end to end, three more
scenarios partially proven. See
`docs/owner/phase8vp4/phase8-final-unconditional-decision.md`. Still CONDITIONAL PASS, still no
final tag -- narrower gap than ever, not yet closed.

## Recommended next session's exact scope

Connect a physical Android device. Confirm `adb devices -l` shows `device`. Install the already-built
rc.3 APKs from `dist/android/{clinic,retail}/` (or `final-android-artifact-evidence.md`'s SHA-256
values, if rebuilding). Run the seven commercial-lifecycle scenarios physically -- all seven are
already proven correct at the Owner/service tier, and Scenario 7 specifically no longer carries any
known Owner-side defect, so this session's expectation is that all seven pass cleanly on real
hardware. Capture Logcat and real traffic per scenario. Write the closing decision. Create the final
tag only if every gate in that decision genuinely passes.
