# Phase 8V-P7 — Baseline

## Entry gates

**Android**: real device, `adb devices -l` -> `1122070476060894 device` (Infinix X6528, Android 13,
API 33). PASS.

**Windows**: this machine is Windows 10 Pro 64-bit. `Get-Command WindowsSandbox.exe` -> not found.
`Get-Service vmms` (Hyper-V management) -> not found/not installed. No second physical Windows machine
is available. **A genuine third client identity (Windows Sandbox/VM/second machine) cannot be created
this session.** Per this session's own governing spec ("If no second genuine client identity can be
created, disclose that Scenarios 6 or 7 may remain blocked"), this is disclosed now, at the start,
rather than discovered late: Scenario 6's full replacement workflow (which needs a genuine *third*
identity, C, replacing B) and Scenario 7's fresh-identity-D activation-block sub-check cannot be
completed with a real, independently-keyed Windows client this session. Where the spec's own harness
exception applies (a real signed activation attempt using the production contract, real keypair, real
Owner signature verification, no direct DB rows) that narrower proof is still pursued; the full
multi-device Windows product experience is not fabricated.

The existing real Retail Windows build (`dist/AuraRetail/AuraRetail.exe`, confirmed present) can still
serve as identity B (a second real product, distinct platform from the Android installation) for
whatever this constraint does allow.

## Git baseline

HEAD `2dcda55`, tree clean, branch `master`. Conditional tag `aura-owner-commercial-ops-phase8-conditional-complete`
dereferences to commit `f593bce7...` (unchanged). Final tag `aura-commercial-licensing-operations-phase8-complete`
does not exist. Original `AuraEnterprise` repo not opened this session.

## Prior docs

This session's author already produced and holds full working knowledge of every phase8vp6 document
(baseline, root-cause, canonical state matrix, extension wiring design, contract decision, build-impact
decision, both scenario finals, regression report) from the immediately preceding session -- re-read
selectively where a specific fact needed re-confirmation rather than wholesale, given no source drift
occurred between sessions (git log confirms HEAD `2dcda55` is exactly where Phase 8V-P6 left it).

Note: this session's request text names some phase8vp6 filenames
(`PHASE8VP6-COMMERCIAL-ENFORCEMENT-CLOSURE-HANDOVER.md`, `phase8-final-unconditional-decision.md`,
`final-residual-risk-register.md`, `final-artifact-report.md`) that do not exist under
`docs/owner/phase8vp6/` -- the actual Phase 8V-P6 output used different filenames
(`phase8vp6-final-decision.md`, no artifact-report was produced since no Retail/Windows rebuild
happened that session). This is disclosed rather than silently substituted. The equivalent, real
residual-risk continuity lives in `docs/owner/phase8vp4/final-residual-risk-register.md`'s additive
Phase 8V-P6 section (updated last session).

## Automated baseline confirmed unchanged since Phase 8V-P6

Owner 398/398, commercial_runtime+licensing_contracts 233/233, Retail 194/194, Clinic 135/135 = 960/960.
Re-run in full as part of this session's own Part T (see `final-regression-report.md`) rather than
merely assumed.

## Known state at session start

Zero P0, zero P1 remaining (both fixed in Phase 8V-P6). One carried P2 (local-deactivation UX,
unchanged, not blocking).
