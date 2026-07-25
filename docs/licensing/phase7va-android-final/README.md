# Phase 7V-A — Final Android Physical Closure (Documentation Index)

This directory documents the final round of Phase 7V-A: converting both Android platform
verdicts (Clinic, Retail) from CONDITIONAL PASS toward PASS via physical device validation on a
real Infinix HOT 40i (Android), strictly inside `aura-fullsuits` (never touching the read-only
`AuraEnterprise` monorepo).

## Files

- `bugs-found-and-fixed.md` — the six real P0/P1 defects found and fixed this session via
  systematic physical debugging, with root cause, fix, and regression test for each.
- `final-artifact-reconfirmation.md` — fresh SHA-256 checksums and certificate-continuity
  reconfirmation for the truly-final Clinic/Retail APK + AAB, built after every fix including the
  Part I offline-lifecycle fix.
- `final-decision.md` — the actual gate-by-gate verdicts for this round, including an honest
  account of which gates reached full physical confirmation and which did not, and why.
- `residual-risks.md` — carried-forward and newly-discovered residual risks, most notably a
  reproducible OEM-level `adb reverse` connectivity limitation on the physical test device that
  blocked live end-to-end confirmation of the suspension and deactivation flows.

## Relationship to earlier Phase 7V documents

This is an ADDITIVE round on top of `docs/licensing/phase7v-final/`. Nothing in `phase7v-final/`
was modified; where this round supersedes a claim made there (e.g. artifact checksums, physical
Android verdicts), the newer document here is authoritative and says so explicitly.
