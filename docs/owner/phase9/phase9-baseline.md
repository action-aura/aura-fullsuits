# Phase 9 — Baseline

## Entry gate (real, this session)

- `git status`: clean.
- `git branch --show-current` (before branching): `master`.
- `git rev-parse HEAD`: `4131e610e8e554193b01c1e923ede00f374100e3` — matches the required Phase 8 final
  commit.
- `aura-commercial-licensing-operations-phase8-complete` -> commit `4131e61...`, message confirms
  Phase 8V-P9 UNCONDITIONAL PASS content.
- `aura-owner-commercial-ops-phase8-conditional-complete` -> commit `f593bce7...` (tag object itself
  hashes to `7150564...`), unmoved.
- Original `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` repository: not opened this session.
- Working branch created: `phase9/secure-staging-and-pilot-readiness`, off `master` at `4131e61`.

## Referenced-document discrepancy (recorded honestly, not silently substituted)

The Phase 9 instruction asked to read four specific files that do not exist under
`docs/owner/phase8vp9/` in this repository:

- `docs/owner/phase8vp9/PHASE8VP9-FINAL-CONTRACT-AND-DEVICE-CLOSURE-HANDOVER.md` — does not exist.
- `docs/owner/phase8vp9/phase8-final-unconditional-decision.md` — does not exist; the real file is
  `docs/owner/phase8vp9/phase8-final-unconditional-decision-vp9.md`.
- `docs/owner/phase8vp9/final-residual-risk-register.md` — does not exist under `phase8vp9/`; the most
  recent real risk register is `docs/owner/phase8vp7/final-residual-risk-register.md`.
- `docs/owner/phase8vp9/cleanup-report.md` — does not exist; Phase 8V-P9's cleanup (stopping the real
  Owner/wire-capture/Windows/proxy processes, clearing `adb reverse`/`forward`) was performed and
  reported inline in that session's final summary, but was never written to its own dedicated file.

Actually read instead (real files, `ls docs/owner/phase8vp9/` confirmed before reading):
`phase8vp9-baseline.md`, `phase8-final-unconditional-decision-vp9.md`, `final-regression-report.md`,
`final-artifact-and-manifest-report.md`, `canonical-discount-and-export-contract.md`,
`stale-assertion-physical-final.md`, `scenario7-device-facing-final.md`,
`temporary-exception-precision-final.md`, `license-pepper-preflight-final.md`.

## Current artifact family (verified from repository evidence, not assumed)

| Product | Platform | Version | versionCode | SHA-256 |
|---|---|---|---|---|
| Retail | Android APK | `1.0.0-rc.5` | `6` | `800e60091b6cd320bd65abe0298ff73bb2a455756c4eee2ec3a1266d679daebd` |
| Clinic | Android APK | `1.0.0-rc.5` | `6` | `1665e7c7973ac65b9479efd04463aea1ca17c396aa3047c268c119f7d8b006a2` |
| Retail | Windows exe | `1.0.0-rc.5` | n/a | `60741cb7c56a41fb1cd8b03c370c907e4a3cc1957571c28f66a62f76639a52fb` |
| Clinic | Windows exe | `1.0.0-rc.5` | n/a | `170c90275a0bfb9c19674b1565e9cc2124b512b5dba72968a0152a20d250e0e9` |

`versionCode` read directly from `android/aura-retail/app/build.gradle` and
`android/aura-clinic/app/build.gradle` (`versionCode 6`), not assumed from the spec's suggested value
— confirms the Phase 8V-P9 bump (5 -> 6) is what is actually in the tree.

Retail cert SHA-256: `cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d`.
Clinic cert SHA-256: `35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2`.

## Test baseline carried in (to be re-run fresh for Phase 9, per Milestone 19 — not reused as final)

Owner 405/405, `commercial_runtime` 235/235, Retail 194/194 (per-file; monolithic run was the known
order-dependent issue — see Milestone 2), Clinic 135/135.

## Infrastructure reality check (governs the whole phase)

This machine (`c:\Users\Dell\Desktop\AuraEnterprise`) is a local Windows development workstation. There
is no cloud/VPS account, no registered domain, and no remote host credentials available in this
session. Per explicit user decision (asked directly at the start of Phase 9), this phase proceeds
**local-only**, using the governing spec's own documented fallback for Milestone 12: build and validate
the complete deployment stack locally, mark remote staging deployment and everything that depends on a
real HTTPS staging URL (Milestones 12-14, and the staging-activation portions of 13) as **NOT
VERIFIED**, and do not create the `aura-secure-staging-phase9-complete` tag this session (the spec
itself forbids that tag without a real deployed, HTTPS-reachable staging host). The expected outcome of
this phase is therefore **CONDITIONAL PASS**, not PASS.
