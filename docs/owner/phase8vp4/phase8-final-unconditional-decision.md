# Phase 8V-P4 — Final Phase 8 Decision

## Per-dimension verdicts

| # | Dimension | Verdict |
|---|---|---|
| 1 | Device readiness | **PASS** |
| 2 | URL-configured rebuild | **PASS** |
| 3 | Clinic APK/AAB | **PASS** |
| 4 | Retail APK/AAB | **PASS** |
| 5 | Certificate continuity | **PASS** |
| 6 | Signed installation/upgrade | **PASS** |
| 7 | Initial activation | **PASS** |
| 8 | Scenario 1 | **PASS** |
| 9 | Scenario 2 | **CONDITIONAL** |
| 10 | Scenario 3 | **CONDITIONAL** |
| 11 | Scenario 4 | **PASS** |
| 12 | Scenario 5 | **CONDITIONAL** |
| 13 | Scenario 6 | **NOT VERIFIED** |
| 14 | Scenario 7 | **NOT VERIFIED** |
| 15 | Renewal without key | **PASS** |
| 16 | Installation continuity | **PASS** |
| 17 | Device-key continuity | **PASS** |
| 18 | Device-slot continuity | **PASS at tested tier** |
| 19 | Assertion refresh | **PASS** |
| 20 | State-version monotonicity | **PASS structurally, not stress-tested** |
| 21 | Stale assertion rejection | **NOT independently stress-tested** |
| 22 | Restricted-to-active restoration | **PASS at subscription tier, not at local-visual tier** |
| 23 | Backend enforcement | **PASS for the one case tested** |
| 24 | Traffic privacy | **PASS for what was captured; raw wire capture not performed** |
| 25 | Logcat privacy | **PASS** |
| 26 | Data preservation | **PASS** |
| 27 | Backup/restore/export | **NOT exercised this session** |
| 28 | Phase 8 overall | **CONDITIONAL PASS** -- real, substantial progress; several disclosed gaps remain |

## Why this is still not an unconditional PASS

This phase's own Part Y is explicit: final PASS requires **all seven scenarios completed**, actual
traffic evidence, and no remaining gap of the kinds listed. Scenarios 2, 3, and 5 are real but
conditional (specific named sub-checks not reached, for genuine structural/timing/single-device
reasons, all disclosed in their own evidence files); Scenarios 6 and 7 were not independently
re-verified physically this session at all (single physical device, mechanics already proven for
real in prior sessions but not re-run from this device). Per the brief's own tag-blocking list
("any scenario is incomplete... traffic or Logcat evidence is missing" -- Logcat is present and
clean, but traffic evidence is partial, not the full raw wire capture requested), the final tag is
**not created**.

## What genuinely changed since Phase 8V-P3 (and every session before it)

For the first time across four consecutive sessions (Phase 8V-P, 8V-P2, 8V-P3, 8V-P4), **a physical
Android device was actually connected and used for real**. This session achieved, for real, on
that physical device:

- A real signed rc.2 -> rc.3 in-place upgrade, both products, certificate identity unchanged.
- A real, genuine, first-ever Ed25519-signed physical activation, both products, cross-verified on
  both the device and the real Owner Postgres database.
- Two fully real end-to-end commercial scenarios (early renewal, pilot conversion) with complete,
  disclosed evidence.
- Three more scenarios (late renewal, past due, emergency extension) with their *core* mechanics
  proven real and their remaining sub-checks honestly disclosed as not reached, not fabricated.
- Real, physical persistence (force-stop/reopen), real backend enforcement (`LICENSE_INACTIVE`),
  real data preservation (a real patient record and a real $100.00 sale with correct stock
  mutation), and a clean real Logcat/traffic review for everything actually exercised.
- One real, previously undocumented environment gap (a dropped `adb reverse` tunnel, mistaken
  initially for a build defect) found, root-caused, and resolved without any source change.
- One real, new, low-severity UX finding (no in-app reactivation path after explicit local device
  deactivation) -- disclosed, not fixed (out of this validation-only phase's scope).
- Zero P0, zero P1.

The remaining gap has narrowed from "no physical device at all" (three prior sessions) to a
specific, bounded list: two scenarios needing a second device identity, and several named
sub-checks within three other scenarios needing either more session time or different starting
conditions than this session's own real state permitted.

## Recommended next session's exact scope

1. Re-run this same physical device (already signed, already activated, already has real synthetic
   data) for the specific unreached sub-checks: let an already-issued assertion naturally expire (or
   start a fresh scenario from an already-bad subscription) to observe the real local RESTRICTED
   transition; exercise the Retail POS discount+tax input if it exists elsewhere in the UI, or
   confirm it doesn't and treat that as its own finding.
2. Stand up a second real device identity (wire-level, per this phase's own explicit allowance) for
   Scenarios 6 and 7.
3. Add real wire-level traffic capture (a local TLS-terminating proxy or Kotlin-side structured
   logging) for the raw Owner<->device bytes specifically.
4. Exercise backup/restore/export on-device for real.
5. Only once every named gap above closes, write the closing decision and create the final tag.

## Phase 8V-P5 update (additive; this document's own historical verdict stands unchanged)

Phase 8V-P5 made real progress on several of the above items: root-caused why local RESTRICTED had
never been observed, achieved it physically for the first time (Clinic), added real wire-capture
infrastructure, found and fixed a real P1 timezone bug, found and disclosed a real emergency-extension
wiring gap, and ran the full regression including product backends (943/943). Scenarios 2 (Retail-side
restricted-to-active/88.00/returns), 6, and 7 remain open, along with stale-assertion rejection and
backup/restore/export. The final unconditional tag remains withheld. See
`docs/owner/phase8vp5/phase8-final-decision.md` for the complete, current per-dimension verdict, which
supersedes this document's "next session's exact scope" section above as the current source of truth
for what remains.
